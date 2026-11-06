"""开发用：验证「把 Edge app 窗口塞进 Qt 窗口当子窗口」是否可行。

可行的话就能做到：HTML/CSS 原样渲染（100% 保真）+ 不带 Chromium 内核（0 体积）
+ 没有标题栏（把子窗口向上偏移，让标题栏跑到父窗口裁剪区外）。

    python ORBYTE/tools/test_embed_edge.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
W, H, X, Y = 620, 760, 300, 100

HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>ORBYTE</title></head>
<body style="margin:0;height:100vh;background:linear-gradient(160deg,#061A38,#02081C);
color:#DFF3FF;font-family:'Microsoft YaHei UI';display:flex;align-items:center;justify-content:center">
<div style="text-align:center">
  <div style="font-size:30px;font-weight:700;letter-spacing:6px;color:#22D7FF">ORBYTE</div>
  <div style="margin-top:10px;font-size:13px;color:#8FB4D4">嵌入 Qt 窗口 · 无标题栏</div>
  <div style="margin-top:20px;width:240px;height:6px;border-radius:3px;
       background:linear-gradient(90deg,#22D7FF,#137BFF)"></div>
</div></body></html>"""


def enum_edge_window(title: str, timeout: float = 10.0) -> int | None:
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        hit: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            if buf.value.strip() == title and "Chrome_WidgetWin" in cls.value:
                hit.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        if hit:
            return hit[0]
        time.sleep(0.4)
    return None


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QWidget

    app = QApplication([])
    parent = QWidget()
    parent.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    parent.setStyleSheet("background:#0C1B35;")
    parent.resize(W, H)
    parent.move(X, Y)
    parent.show()
    app.processEvents()

    tmp = Path(tempfile.mkdtemp())
    page = tmp / "probe.html"
    page.write_text(HTML, encoding="utf-8")
    proc = subprocess.Popen([
        EDGE,
        f"--app={page.as_uri()}",
        f"--window-size={W},{H}",
        "--window-position=-32000,-32000",   # 先放到屏幕外，避免闪现
        "--user-data-dir=" + str(tmp / "profile"),
        "--no-first-run",
        "--no-default-browser-check",
    ])

    child = enum_edge_window("ORBYTE")
    if not child:
        print("没找到 Edge 窗口")
        proc.terminate()
        return 2
    print("Edge 窗口 =", child)

    user32 = ctypes.windll.user32
    WS_CHILD, WS_POPUP = 0x40000000, 0x80000000
    GWL_STYLE = -16
    style = user32.GetWindowLongW(child, GWL_STYLE)
    user32.SetWindowLongW(child, GWL_STYLE, (style | WS_CHILD) & ~WS_POPUP)

    parent_hwnd = int(parent.winId())
    ok = user32.SetParent(child, wintypes.HWND(parent_hwnd))
    print("SetParent 返回 =", ok)

    rect = wintypes.RECT()
    user32.GetWindowRect(child, ctypes.byref(rect))
    chrome_height = 34  # Edge app 窗口自带的标题条高度
    user32.SetWindowPos(child, 0, 0, -chrome_height, W, H + chrome_height,
                        0x0004 | 0x0040)  # SWP_NOZORDER | SWP_SHOWWINDOW

    for _ in range(60):
        app.processEvents()
        time.sleep(0.05)
    time.sleep(1.5)

    img = app.primaryScreen().grabWindow(0, X, Y, W, H)
    out = _ROOT / "docs" / "embed-edge-probe.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG")
    print("截图:", out)
    print("中心像素 =", img.toImage().pixelColor(W // 2, H // 2).name())
    print("顶部像素 =", img.toImage().pixelColor(W // 2, 6).name())

    time.sleep(0.5)
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
