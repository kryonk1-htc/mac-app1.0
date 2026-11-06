"""系统 WebView2 的极薄 COM 封装（ctypes，零额外依赖）。

只用到三个调用，其余一概不碰：
    CreateCoreWebView2EnvironmentWithOptions   （1 个回调）
    ICoreWebView2Controller::put_Bounds / put_IsVisible
    ICoreWebView2::Navigate

为什么这么克制：WebView2 的完整 API 有几百个方法，每个都要在 Python 里
手搓 COM vtable。只用这三个，就把"手搓 COM"的风险压到一个小文件里。
JS ↔ Python 的通信走本地 HTTP（见 bridge.py），完全不需要 WebMessage 事件。

⚠️ 两个必须记住的事实：
1. COM 接口是**只增不改**的，所以方法在 vtable 里的下标对所有版本都稳定。
   下面这些下标是从 WebView2.h 的接口声明顺序数出来的，不要"优化"。
2. 回调是通过窗口消息派发的，必须在同一线程里**主动泵消息**，
   否则 CreateEnvironment 的回调永远不执行（Qt 的事件循环不保证派发
   线程消息，所以这里提供 pump_messages()，由调用方定时调用）。
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import POINTER, byref, c_int, c_uint, c_void_p, c_wchar_p, wintypes
from pathlib import Path

logger = logging.getLogger("orb.webview2")

HRESULT = ctypes.c_long
S_OK = 0

# ---- 载入 WebView2Loader ----

_LOADER_CANDIDATES = (
    "{vendor}/WebView2Loader.dll",
    r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\{version}\WebView2Loader.dll",
    r"C:\Program Files\Microsoft Office\root\Office16\WebView2Loader.dll",
    r"C:\Program Files\Microsoft OneDrive\{version}\WebView2Loader.dll",
)


def _candidate_paths() -> list[Path]:
    """找 WebView2Loader.dll：先看随包分发的 vendor，再用系统里现成的。

    新版 Edge 运行时目录里**不再带**这个 dll（微软把它挪进了 SDK），
    所以随包带一份是最稳的；另外几条系统路径只是开发和兜底用。
    """
    paths: list[Path] = []
    here = Path(__file__).resolve()
    for parent in here.parents:
        vendor = parent / "vendor" / "WebView2Loader.dll"
        if vendor.exists():
            paths.append(vendor)
        if parent.name.lower() == "orbyte":
            break
    for base in (
        r"C:\Program Files\Microsoft Office\root\Office16",
        r"C:\Program Files\Microsoft OneDrive",
        r"C:\Program Files (x86)\Lenovo\PCManager\5.1.200.8201",
    ):
        direct = Path(base) / "WebView2Loader.dll"
        if direct.exists():
            paths.append(direct)
        else:
            # OneDrive 这类目录带版本号，扫一层
            try:
                for child in Path(base).iterdir():
                    candidate = child / "WebView2Loader.dll"
                    if candidate.exists():
                        paths.append(candidate)
                        break
            except OSError:
                continue
    return paths


_loader: ctypes.WinDLL | None = None


def _load() -> ctypes.WinDLL | None:
    global _loader
    if _loader is not None:
        return _loader
    for candidate in _candidate_paths():
        try:
            _loader = ctypes.WinDLL(str(candidate))
            logger.info("WebView2Loader 已加载：%s", candidate)
            return _loader
        except OSError:
            continue
    logger.warning("未找到 WebView2Loader.dll")
    return None


def runtime_version() -> str | None:
    """系统 WebView2 运行时版本；None 表示没装。"""
    lib = _load()
    if lib is None:
        return None
    fn = lib.GetAvailableCoreWebView2BrowserVersionString
    fn.argtypes = [wintypes.LPCWSTR, POINTER(wintypes.LPWSTR)]
    fn.restype = HRESULT
    out = wintypes.LPWSTR()
    hr = fn(None, byref(out))
    if hr != S_OK or not out.value:
        return None
    return out.value


def available() -> bool:
    return runtime_version() is not None


# ---- COM 基础设施 ----


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _UnknownStruct(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(c_void_p))]


class _Handler:
    """手搓的 COM 对象：只实现 IUnknown 三件套 + Invoke。

    QueryInterface 对任何 IID 都返回自己——WebView2 会用它自己的 IID 来问，
    我们不做类型区分（这是 handler 场景下公认可行的简化）。
    """

    _keep: list["_Handler"] = []

    def __init__(self, invoke):
        self._invoke_impl = invoke
        self._obj = _UnknownStruct()
        # 0:QueryInterface 1:AddRef 2:Release 3:Invoke
        self._fns = (
            ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(_GUID), POINTER(c_void_p))(
                self._query_interface
            ),
            ctypes.WINFUNCTYPE(c_uint, c_void_p)(self._add_ref),
            ctypes.WINFUNCTYPE(c_uint, c_void_p)(self._release),
            ctypes.WINFUNCTYPE(HRESULT, c_void_p, HRESULT, c_void_p)(self._invoke),
        )
        vtable = (c_void_p * 4)(
            *[ctypes.cast(fn, c_void_p).value for fn in self._fns]
        )
        self._vtable = vtable  # 必须持有，否则被 GC 掉就是野指针
        self._obj.lpVtbl = ctypes.cast(vtable, POINTER(c_void_p))
        self.ptr = ctypes.cast(byref(self._obj), c_void_p)
        _Handler._keep.append(self)

    # COM 方法
    def _query_interface(self, this, riid, ppv):
        ppv[0] = self.ptr
        return S_OK

    def _add_ref(self, this):
        return 1

    def _release(self, this):
        return 1  # 故意不释放：由 Python 侧持有生命周期，避免 UAF

    def _invoke(self, this, result, arg):
        try:
            self._invoke_impl(result, arg)
        except Exception:  # noqa: BLE001 - 异常绝不能穿过 C 边界
            logger.exception("COM 回调异常")
        return S_OK


def _vt_call(ptr: int, index: int, restype, *argtypes):
    """按 vtable 下标取方法并调用。

    ⚠️ 必须把取到的函数地址转成 int 再交给 WINFUNCTYPE：
    直接传 c_void_p 对象时 ctypes 会把它当成"地址的地址"，
    调用会跳到野指针上（表现为 access violation，极难定位）。
    """
    vtable = ctypes.cast(ctypes.c_void_p(ptr), POINTER(POINTER(c_void_p)))[0]
    address = vtable[index]
    if not address:
        raise RuntimeError(f"vtable[{index}] 为空，接口下标可能不对")
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(int(address))


def _add_ref(ptr: int) -> None:
    """必须自己 AddRef：回调里拿到的接口指针只在回调期间有效，
    不 AddRef 的话回调一返回对象就被释放，后续调用全是野指针
    （表现为 access violation 或者一个莫名其妙失败的 HRESULT）。"""
    try:
        _vt_call(ptr, 1, c_uint)(ptr)
    except Exception:  # noqa: BLE001
        logger.exception("AddRef 失败")


def _release(ptr: int) -> None:
    try:
        _vt_call(ptr, 2, c_uint)(ptr)
    except Exception:  # noqa: BLE001
        logger.exception("Release 失败")


def pump_messages() -> None:
    """泵一次线程消息。WebView2 的回调靠它派发，必须定期调用。"""
    msg = wintypes.MSG()
    while ctypes.windll.user32.PeekMessageW(byref(msg), 0, 0, 0, 1):  # PM_REMOVE
        ctypes.windll.user32.TranslateMessage(byref(msg))
        ctypes.windll.user32.DispatchMessageW(byref(msg))


# ---- 主封装 ----


_com_ready = False


def _init_com() -> None:
    """必须先把当前线程初始化成 STA。

    少了这一步，CreateCoreWebView2EnvironmentWithOptions 会直接失败
    （CO_E_NOTINITIALIZED），而错误表现是"卡住不动"，很难看出根因。
    RPC_E_CHANGED_MODE(0x80010106) 说明线程已经是别的模式，忽略即可。
    """
    global _com_ready
    if _com_ready:
        return
    hr = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if hr not in (0, 1, -2147417850):  # S_OK / S_FALSE / RPC_E_CHANGED_MODE
        logger.warning("CoInitializeEx 返回 0x%08x", hr & 0xFFFFFFFF)
    _com_ready = True


class WebView2:
    """把 WebView2 绑到一个已有 HWND 上，用于渲染本地面板。"""

    def __init__(self, parent_hwnd: int, user_data_dir: Path) -> None:
        self._hwnd = parent_hwnd
        self._user_data = Path(user_data_dir)
        self._env: int | None = None
        self._controller: int | None = None
        self._webview: int | None = None
        self._closed = False

    # ---- 创建 ----

    def create(self, url: str, timeout: float = 20.0) -> bool:
        _init_com()
        lib = _load()
        if lib is None:
            return False
        self._user_data.mkdir(parents=True, exist_ok=True)

        fn = lib.CreateCoreWebView2EnvironmentWithOptions
        fn.argtypes = [
            wintypes.LPCWSTR,  # browserExecutableFolder
            wintypes.LPCWSTR,  # userDataFolder
            c_void_p,          # options
            c_void_p,          # handler
        ]
        fn.restype = HRESULT

        done = {"ok": False}

        def on_env(result: int, env_ptr: int) -> None:
            if result != S_OK or not env_ptr:
                logger.error("创建 WebView2 环境失败 hr=0x%08x", result & 0xFFFFFFFF)
                self._env = None
                done["ok"] = True
                return
            _add_ref(env_ptr)
            self._env = env_ptr
            # ⚠️ 这里**不能**接着去 CreateController：这个回调是在 fn() 内部
            # 同步调用的，在里面再泵消息会造成嵌套泵、直接卡死。
            done["ok"] = True

        handler = _Handler(on_env)
        hr = fn(None, str(self._user_data), None, ctypes.cast(handler.ptr, c_void_p))
        if hr != S_OK:
            logger.error("CreateCoreWebView2EnvironmentWithOptions hr=0x%08x", hr & 0xFFFFFFFF)
            return False

        deadline = time.time() + timeout
        while not done["ok"] and time.time() < deadline:
            pump_messages()
            time.sleep(0.01)
        if self._env is None:
            return False

        # 环境就绪后再建控制器（顺序执行，不在回调里嵌套）
        self._create_controller()
        if self._controller is None:
            return False

        self.navigate(url)
        return True

    def _create_controller(self) -> None:
        env = self._env
        if not env:
            return
        done = {"ok": False}

        def on_controller(result: int, controller_ptr: int) -> None:
            if result != S_OK or not controller_ptr:
                logger.error("创建 Controller 失败 hr=0x%08x", result & 0xFFFFFFFF)
                done["ok"] = True
                return
            _add_ref(controller_ptr)
            self._controller = controller_ptr
            # 同样不能在这里取 CoreWebView2：在自己的回调里调用会拿到无效指针
            done["ok"] = True

        handler = _Handler(on_controller)
        # env vtable 下标 3：CreateCoreWebView2Controller
        _vt_call(env, 3, HRESULT, wintypes.HWND, c_void_p)(
            env, wintypes.HWND(self._hwnd), ctypes.cast(handler.ptr, c_void_p)
        )

        deadline = time.time() + 20.0
        while not done["ok"] and time.time() < deadline:
            pump_messages()
            time.sleep(0.01)

        if self._controller:
            # 回调返回后再取 CoreWebView2（controller vtable 下标 25）
            out = c_void_p()
            hr = _vt_call(self._controller, 25, HRESULT, POINTER(c_void_p))(
                self._controller, byref(out)
            )
            if hr == S_OK and out.value:
                self._webview = out.value
            else:
                logger.error("get_CoreWebView2 失败 hr=0x%08x", hr & 0xFFFFFFFF)
            self.set_visible(True)

    # ---- 控制 ----

    def set_bounds(self, left: int, top: int, right: int, bottom: int) -> None:
        if not self._controller or self._closed:
            return
        rect = _RECT(int(left), int(top), int(right), int(bottom))
        # controller vtable 下标 6：put_Bounds
        _vt_call(self._controller, 6, HRESULT, _RECT)(
            self._controller, rect
        )

    def set_visible(self, visible: bool) -> None:
        if not self._controller or self._closed:
            return
        # controller vtable 下标 4：put_IsVisible
        _vt_call(self._controller, 4, HRESULT, c_int)(
            self._controller, 1 if visible else 0
        )

    def navigate(self, url: str) -> None:
        if not self._webview or self._closed:
            return
        # webview vtable 下标 13：Navigate
        hr = _vt_call(self._webview, 13, HRESULT, c_wchar_p)(
            self._webview, url
        )
        if hr != S_OK:
            logger.error("Navigate 失败 hr=0x%08x", hr & 0xFFFFFFFF)
        else:
            logger.info("面板已加载：%s", url)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._controller:
                # controller vtable 下标 24：Close
                _vt_call(self._controller, 24, HRESULT)(self._controller)
        except Exception:  # noqa: BLE001
            logger.exception("关闭 WebView2 失败")
        self._controller = None
        self._webview = None
        self._env = None
