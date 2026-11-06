"""开发用：单独预览主面板并截屏。

    python ORBYTE/tools/preview_panel.py [输出png]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.environ.setdefault(
    "ORBYTE_DB_PATH", str(Path(tempfile.mkdtemp()) / "preview.db")
)

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte import todo_store  # noqa: E402
from orbyte.db import database  # noqa: E402
from orbyte.ui.panel import PANEL_H, PANEL_W, Panel  # noqa: E402


def seed() -> None:
    database.init_schema()
    if todo_store.list_todos("work"):
        return
    todo_store.add("把周报的图表补上，下午例会要用", "日清填报")
    todo_store.add("和产品确认待办插件的同步字段", "ORBYTE")
    todo_store.add("整理三季度的项目复盘材料", "团队分析")
    todo_store.add("回一下法务关于数据留存的口径", "合规")
    todo_store.add("买猫粮", "生活", space="life")
    items = todo_store.list_todos("work")
    todo_store.set_state(items[1]["uuid"], "doing")
    todo_store.set_state(items[2]["uuid"], "done")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else _ROOT / "docs" / "panel-preview.png"
    seed()
    app = QApplication(sys.argv)
    screen = app.primaryScreen()

    panel = Panel()
    panel.move(240, 90)
    panel.show()
    panel.set_sync_status({"state": "idle", "pending": 3})

    def shoot() -> None:
        geo = panel.frameGeometry()
        img = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out), "PNG")
        print("截图:", out.resolve())
        print("中心像素 =", img.toImage().pixelColor(PANEL_W // 2, PANEL_H // 2).name())
        app.quit()

    QTimer.singleShot(1600, shoot)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
