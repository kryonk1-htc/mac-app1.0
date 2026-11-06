# -*- mode: python ; coding: utf-8 -*-
"""ORBYTE 打包描述（用 spec 而不是纯命令行）。

为什么要自己维护 spec：
`--exclude-module` 只能排除 **Python 模块**，拦不住「Qt 插件 DLL 顺着依赖
链把别的 Qt 模块拖进来」这件事。实测 conda 版 PyQt5 里：

    imageformats/qpdf.dll            → Qt5Pdf_conda.dll        (4.4MB)
    platforminputcontexts/qtvirtualkeyboard.dll
                                     → Qt5VirtualKeyboard / Qt5Quick / Qt5Qml (8.5MB+)

这些都跟我们的原生控件毫无关系，但 PyInstaller 的二进制依赖分析会老老实实
全部打进去。所以这里在 Analysis 之后直接过滤 a.binaries——比在命令行里
猜模块名可靠，也是这批"多出来 13MB"的唯一解法。

不动的部分：Qt5Core/Gui/Widgets/Network、ICU 三件套、OpenSSL。
Qt5Core 的导入表里实打实链着 icuuc/icuin，删掉程序直接起不来。

    python -m PyInstaller --noconfirm --clean orbyte.spec
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH 由 PyInstaller 注入

# ---- 不要的 Qt 二进制（按文件名）----
_DROP_DLLS = {
    "qt5pdf_conda.dll",
    "qt5quick_conda.dll",
    "qt5qml_conda.dll",
    "qt5qmlmodels_conda.dll",
    "qt5virtualkeyboard_conda.dll",
    "qt5websockets_conda.dll",
    "qt5dbus_conda.dll",
    "qt5networkauth_conda.dll",
    "qt5winextras_conda.dll",
    "qt5quickwidgets_conda.dll",
}

# ---- 不要的 Qt 插件（按路径片段）----
_DROP_PATH_PARTS = (
    "platforminputcontexts",   # 虚拟键盘输入法：桌面端用系统输入法
    "imageformats/qpdf",       # PDF 当图片读？没有这个需求
    "platforms/qwebgl",
    "platforms/qdirect2d",     # 用默认的 qwindows 就够
    "platforms/qminimal",
    "platforms/qoffscreen",
    "generic/",
    "iconengines/",
)


def _keep(entry) -> bool:
    name = str(entry[0])
    source = str(entry[1]).replace("\\", "/").lower()
    base = name.replace("\\", "/").split("/")[-1].lower()
    if base in _DROP_DLLS:
        return False
    return not any(part in source for part in _DROP_PATH_PARTS)


a = Analysis(  # noqa: F821
    [str(ROOT / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # 资源只留图标；早期 WebView2 面板（web/panel.html）已经不用了
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
        "PyQt5.QtTest", "PyQt5.QtDesigner", "PyQt5.QtHelp", "PyQt5.QtOpenGL",
        "PyQt5.QtSql", "PyQt5.QtXml", "PyQt5.QtXmlPatterns", "PyQt5.QtDBus",
        "PyQt5.QtQuick", "PyQt5.QtQuickWidgets", "PyQt5.QtQml",
        # 用不到的重型标准库与三方包
        "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
        "numpy", "pandas", "matplotlib", "scipy", "PIL", "IPython", "notebook",
    ],
    noarchive=False,
    optimize=0,
)

_before = len(a.binaries)
a.binaries = [entry for entry in a.binaries if _keep(entry)]
print(f"[orbyte.spec] 过滤掉 {_before - len(a.binaries)} 个无用二进制")

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ORBYTE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩会显著提高杀软误报率，省下的几 MB 不值
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / "src" / "orbyte" / "assets" / "orbyte.ico")],
)
