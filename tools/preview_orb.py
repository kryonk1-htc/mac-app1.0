"""开发用：单独预览悬浮球，并截屏验证真透明。

用法：
    cd ORBYTE/src && python -m tools.preview_orb [输出路径]
或者：
    python ORBYTE/tools/preview_orb.py

它做三件事：
1. 把球显示在屏幕上
2. 截屏该区域（含桌面背景）——用来确认四角透明，不是黑块
3. 打印四角像素与桌面对照
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte.ui.orb import MARGIN, ORB_SIZE, Orb  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("orb-preview.png")
    app = QApplication(sys.argv)
    screen = app.primaryScreen()

    orb = Orb()
    x, y = 260, 240
    orb.move(x, y)
    orb.start_animation()
    orb.show()

    def shoot() -> None:
        geo = orb.frameGeometry()
        shot = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        shot.save(str(out), "PNG")
        img = shot.toImage()
        dpr = img.width() / float(max(1, geo.width()))
        # 窗口四角：透明处应与桌面原色一致（这里只报 alpha）
        corner = img.pixelColor(2, 2)
        center = img.pixelColor(
            int((MARGIN + ORB_SIZE / 2) * dpr), int((MARGIN + ORB_SIZE / 2) * dpr)
        )
        print(f"截图已保存：{out.resolve()}")
        print(f"左上角像素 alpha = {corner.alpha()}（0 或与桌面一致 = 真透明）")
        print(f"球心像素 = {center.name()} alpha={center.alpha()}")
        app.quit()

    QTimer.singleShot(2200, shoot)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
