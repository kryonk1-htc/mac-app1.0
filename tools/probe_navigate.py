"""开发用：探测 ICoreWebView2 里哪个 vtable 下标能渲染内容。

每次只测一个下标，由外部脚本循环调用——因为错的下标会直接崩进程，
所以必须一个下标一个进程，崩了不影响继续。

    python ORBYTE/tools/probe_navigate.py <index>
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
from ctypes import POINTER, byref, c_void_p, c_wchar_p, wintypes
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

import orbyte.webview2 as m  # noqa: E402

INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 13
W, H, X, Y = 320, 220, 120, 120

HTML = (
    '<html><body style="margin:0;background:#00FF00;color:#000;'
    'font-size:28px;font-family:Consolas">OK</body></html>'
)


def main() -> int:
    app = QApplication([])
    holder = QWidget()
    holder.setWindowFlags(Qt.FramelessWindowHint)
    holder.setStyleSheet("background:#101010;")
    holder.resize(W, H)
    holder.move(X, Y)
    holder.show()
    app.processEvents()

    view = m.WebView2(int(holder.winId()), Path(tempfile.mkdtemp()) / "data")
    m._init_com()
    lib = m._load()
    fn = lib.CreateCoreWebView2EnvironmentWithOptions
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, c_void_p, c_void_p]
    fn.restype = ctypes.c_long

    box = {"ok": False}

    def env_cb(result, ptr):
        m._add_ref(ptr)
        view._env = ptr
        box["ok"] = True

    handler = m._Handler(env_cb)
    fn(None, str(Path(tempfile.mkdtemp()) / "ud"), None, ctypes.cast(handler.ptr, c_void_p))
    while not box["ok"]:
        m.pump_messages()
        time.sleep(0.01)

    view._create_controller()
    webview = view._webview
    view.set_bounds(0, 0, W, H)

    try:
        # 先按 LPCWSTR 试（Navigate / NavigateToString 都是这个签名）
        hr = m._vt_call(webview, INDEX, m.HRESULT, c_wchar_p)(webview, HTML)
        print(f"idx={INDEX} hr=0x{hr & 0xFFFFFFFF:08x}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"idx={INDEX} 异常 {exc}", flush=True)
        return 1

    for _ in range(200):
        m.pump_messages()
        time.sleep(0.01)
    app.processEvents()
    img = app.primaryScreen().grabWindow(0, X, Y, W, H)
    px = img.toImage().pixelColor(W // 2, H // 2).name()
    print(f"idx={INDEX} 中心像素={px}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
