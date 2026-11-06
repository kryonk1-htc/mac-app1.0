"""开发用：验证"用系统 Edge 的 app 窗口当面板"这条路。

思路：不留 Chromium 内核（省 107MB），面板直接交给系统 Edge 渲染，
再用 user32 把标题栏去掉。跑法：

    python ORBYTE/tools/test_edge_panel.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>ORBYTE</title></head>
<body style="margin:0;height:100vh;background:linear-gradient(160deg,#061A38,#02081C);
color:#22D7FF;font-family:'Microsoft YaHei UI';display:flex;align-items:center;justify-content:center">
<div style="text-align:center">
  <div style="font-size:34px;font-weight:700;letter-spacing:6px">ORBYTE</div>
  <div style="margin-top:8px;font-size:13px;color:#8FB4D4">Edge app 窗口 · 已去标题栏</div>
</div></body></html>"""


def find_edge() -> str | None:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def main() -> int:
    edge = find_edge()
    if not edge:
        print("没找到 msedge.exe")
        return 2
    print("Edge =", edge)

    tmp = Path(tempfile.mkdtemp())
    page = tmp / "probe.html"
    page.write_text(HTML, encoding="utf-8")

    args = [
        edge,
        f"--app={page.as_uri()}",
        "--window-size=760,880",
        "--window-position=240,90",
        "--user-data-dir=" + str(tmp / "profile"),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeSidebarV2",
    ]
    proc = subprocess.Popen(args)
    time.sleep(7)

    user32 = ctypes.windll.user32
    found: list[tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "Chrome_WidgetWin" not in cls.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        found.append((hwnd, title.value, cls.value))
        return True

    user32.EnumWindows(callback, 0)
    print("Chrome 窗口候选:", found)
    if not found:
        proc.terminate()
        return 3

    hwnd = found[0][0]
    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_MAXIMIZEBOX = 0x00010000
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    print("原样式 = 0x%08x" % (style & 0xFFFFFFFF))
    new_style = style & ~WS_CAPTION & ~WS_THICKFRAME & ~WS_MAXIMIZEBOX
    user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
    user32.SetWindowPos(
        hwnd, 0, 240, 90, 760, 880, 0x0020 | 0x0004  # SWP_FRAMECHANGED | SWP_NOZORDER
    )
    time.sleep(1)
    print("去边框后 = 0x%08x" % (user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF))

    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    img = app.primaryScreen().grabWindow(0, 240, 90, 760, 880)
    out = Path(__file__).resolve().parent.parent / "docs" / "edge-panel-probe.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG")
    print("截图:", out)
    print("中心像素 =", img.toImage().pixelColor(380, 440).name())

    time.sleep(1)
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
