# -*- mode: python ; coding: utf-8 -*-
"""ORBYTE 的 macOS 打包描述 → ORBYTE.app

在 macOS 上执行：

    python -m PyInstaller --noconfirm --clean orbyte-mac.spec

为什么不复用 orbyte.spec：两者有三处本质差别，混在一起两边都会坏。

1. **产物形态**：Windows 是单文件 exe；macOS 必须是 .app 包
   （EXE(exclude_binaries=True) + COLLECT + BUNDLE）。mac 上的单文件模式
   出来的是个 Unix 可执行文件，Finder 里双击打不开，也没有图标和 URL scheme。

2. **二进制过滤**：orbyte.spec 靠"按 .dll 文件名 + platforms/qwindows 路径"
   删掉多余的 Qt 模块。macOS 上这些文件叫 .dylib（platforms/libqcocoa.dylib
   等），那套规则一条都匹配不上，反过来还可能把平台插件删掉——删了
   libqcocoa.dylib 就是"一启动就退出、什么都不打印"。所以这里**不做**
   a.binaries 过滤，只靠 excludes 排 Python 模块。包大一点，先保证能跑。

3. **系统集成**：orbyte:// 协议在 macOS 靠 Info.plist 的 CFBundleURLTypes，
   不是注册表；开机自启走 LaunchAgents，运行时也不碰全局状态。
"""

import re
from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH 由 PyInstaller 注入

# ---- 版本号：单一来源仍是 src/orbyte/__init__.py ----
_VERSION = "1.0.0"
_init_file = ROOT / "src" / "orbyte" / "__init__.py"
if _init_file.exists():
    _m = re.search(r'__version__\s*=\s*"([^"]+)"', _init_file.read_text(encoding="utf-8"))
    if _m:
        _VERSION = _m.group(1)

#: 图标：workflow 里的 make_icons.py 会生成；没有就交给 PyInstaller 用默认图标，
#: 不让"缺图标"阻断整个构建
_ICNS = ROOT / "src" / "orbyte" / "assets" / "orbyte.icns"

a = Analysis(  # noqa: F821
    [str(ROOT / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # 资源只留图标；web/ 与 vendor/WebView2Loader.dll 都是废弃的 WebView2 方案
    datas=[(str(ROOT / "src" / "orbyte" / "assets"), "orbyte/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 浏览器内核：本方案不用，排掉省 100MB+
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngine", "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebChannel", "PyQt5.QtWebSockets",
        # 没用到的 Qt 模块
        "PyQt5.Qt3DCore", "PyQt5.Qt3DRender", "PyQt5.Qt3DInput", "PyQt5.Qt3DLogic",
        "PyQt5.Qt3DAnimation", "PyQt5.Qt3DExtras",
        "PyQt5.QtCharts", "PyQt5.QtDataVisualization", "PyQt5.QtMultimedia",
        "PyQt5.QtMultimediaWidgets", "PyQt5.QtBluetooth", "PyQt5.QtNfc",
        "PyQt5.QtPositioning", "PyQt5.QtLocation", "PyQt5.QtSerialPort",
        "PyQt5.QtTest", "PyQt5.QtDesigner", "PyQt5.QtHelp",
        "PyQt5.QtSql", "PyQt5.QtXml", "PyQt5.QtXmlPatterns", "PyQt5.QtDBus",
        "PyQt5.QtQuick", "PyQt5.QtQuickWidgets", "PyQt5.QtQml",
        # 用不到的重型标准库与三方包
        "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
        "numpy", "pandas", "matplotlib", "scipy", "IPython", "notebook",
        # 项目内已废弃、不在 import 链上的模块
        "orbyte.webview2", "orbyte.bridge", "orbyte.ui.panel", "orbyte.ui.settings",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # .app 里二进制放 Contents/Frameworks，不塞进入口
    name="ORBYTE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX 在 mac 上会破坏签名与 dylib 加载
    console=False,           # 分发版无终端；调试时临时改 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,        # None = 跟随构建机的架构（macos-14 runner 是 arm64）
    codesign_identity=None,  # 不签名：同事首次右键→打开即可
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ORBYTE",
)

app = BUNDLE(  # noqa: F821
    coll,
    name="ORBYTE.app",
    icon=str(_ICNS) if _ICNS.exists() else None,
    bundle_identifier="com.orbyte.desktop",
    version=_VERSION,
    info_plist={
        "CFBundleName": "ORBYTE",
        "CFBundleDisplayName": "ORBYTE 待办",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": "ORBYTE",
        # orbyte:// 协议：网页上点「打开 ORBYTE」靠这里注册（Windows 是注册表）
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "com.orbyte.desktop.url",
                "CFBundleURLSchemes": ["orbyte"],
            }
        ],
    },
)
