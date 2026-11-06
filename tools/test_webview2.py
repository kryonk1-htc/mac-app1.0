"""开发用：单独验证 WebView2 宿主能否把网页画到 Qt 窗口里。

这是整个方案里唯一有技术风险的一步（手搓 COM），所以单独一个可跑的探针：
    python ORBYTE/tools/test_webview2.py

成功判据：截图上能看到页面里的色块与文字，进程不崩。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from PyQt5.QtCore import QTimer, Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from orbyte import webview2  # noqa: E402

_HTML = """<!DOCTYPE html><html><body style="margin:0;background:#03102F;color:#22D7FF;
font-family:'Microsoft YaHei UI';display:flex;align-items:center;justify-content:center;height:100vh">
<div style="text-align:center">
<div style="font-size:40px;font-weight:700;letter-spacing:4px">ORBYTE</div>
<div style="margin-top:10px;font-size:14px;color:#8FB4D4">WebView2 宿主正常</div>
<div style="margin-top:18px;width:220px;height:8px;border-radius:4px;
background:linear-gradient(90deg,#22D7FF,#137BFF)"></div>
</div></body></html>"""


def main() -> int:
    print("系统 WebView2 版本 =", webview2.runtime_version())
    app = QApplication(sys.argv)
    screen = app.primaryScreen()

    win = QWidget()
    win.setWindowFlags(Qt.FramelessWindowHint)
    win.setStyleSheet("background:#03102F;")
    win.resize(560, 320)
    win.move(220, 200)
    win.show()
    app.processEvents()

    tmp_html = Path(tempfile.mkdtemp()) / "probe.html"
    tmp_html.write_text(_HTML, encoding="utf-8")
    profile = Path(tempfile.mkdtemp()) / "wv2data"

    view = webview2.WebView2(int(win.winId()), profile)
    ok = view.create(tmp_html.as_uri())
    print("create() =", ok, " controller =", bool(view._controller), " webview =", bool(view._webview))
    if not ok:
        return 3
    win.resize(560, 320)
    view.set_bounds(0, 0, win.width(), win.height())

    # 泵消息：WebView2 的异步回调靠它派发
    pump = QTimer()
    pump.timeout.connect(webview2.pump_messages)
    pump.start(10)

    keep = QTimer()
    keep.timeout.connect(lambda: view.set_bounds(0, 0, win.width(), win.height()))
    keep.start(500)

    def shot() -> None:
        geo = win.frameGeometry()
        img = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        out = _ROOT / "docs" / "webview2-probe.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out), "PNG")
        px = img.toImage().pixelColor(280, 60).name()
        print(f"截图：{out}  取样像素={px}（期望偏深蓝，不是纯白）")
        view.close()
        app.quit()

    QTimer.singleShot(5000, shot)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
