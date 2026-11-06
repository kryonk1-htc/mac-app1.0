"""只截挂件一张图——改挂件时的快速自检，比 preview_screens 全套快得多。

    python tools/shot_widget.py [输出路径]
    THEME=dark python tools/shot_widget.py dark.png

⚠️ 必须真实 show()：本机 offscreen 平台没有字体引擎，截出来所有文字都是空白。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["ORBYTE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="orbyte-shot-")) / "p.db")

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte import todo_store  # noqa: E402
from orbyte.db import database  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "shot.png"
MODE = os.getenv("THEME", "light")
TARGET = os.getenv("TARGET", "widget")


def seed() -> None:
    database.init_schema()
    for project in ("权限重构", "日常事务"):
        todo_store.add_project(project)
    plan = [
        ("联调权限模块 12 个接口", "权限重构", "P1", 0),
        ("写接口文档", "权限重构", "", 2),
        ("补旧客户端兼容层", "权限重构", "P2", 1),
        ("报销 10 月差旅", "日常事务", "P2", 0),
        ("约运维过一遍备份策略", "日常事务", "P3", 3),
    ]
    ids = {}
    for title, project, priority, _d in plan:
        item = todo_store.add(title, project, priority=priority)
        ids[title] = item["uuid"]

    # 一条正在计时：底栏要显示在跑
    running = ids["联调权限模块 12 个接口"]
    started = datetime.now(timezone.utc) - timedelta(seconds=761)
    with database.transaction() as conn:
        conn.execute("DELETE FROM active_session")
        conn.execute(
            "INSERT INTO active_session (uuid, started_at) VALUES (?, ?)",
            (running, started.astimezone().isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE todo SET state = 'doing' WHERE uuid = ?", (running,))


def main() -> int:
    app = QApplication([])
    seed()

    from orbyte.ui import theme

    theme.apply(theme.MODE_DARK if MODE == "dark" else theme.MODE_LIGHT)

    if TARGET == "window":
        from orbyte.ui.window import MainWindow

        win = MainWindow()
        win.set_sync_status({"state": "idle", "pending": 3})
        win.refresh()
        win.move(60, 60)
        win.show()

        def shoot_detail() -> None:
            detail_out = OUT.with_name(OUT.stem + "-detail" + OUT.suffix)
            win.grab().save(str(detail_out))
            print(f"saved {detail_out}")
            win.hide()
            app.quit()

        def shoot_window() -> None:
            win.grab().save(str(OUT))
            print(f"saved {OUT}  {win.width()}x{win.height()}")
            items = todo_store.list_todos(todo_store.SPACE_WORK)
            if items:
                win.show_detail(items[0]["uuid"])
                QTimer.singleShot(500, shoot_detail)
            else:
                win.hide()
                app.quit()

        QTimer.singleShot(700, shoot_window)
        app.exec_()
        return 0

    from orbyte.ui.widget import Widget

    panel = Widget()
    panel.move(120, 120)
    panel.show()

    def shoot() -> None:
        panel.grab().save(str(OUT))
        print(f"saved {OUT}  {panel.width()}x{panel.height()}")
        panel.hide()
        app.quit()

    QTimer.singleShot(700, shoot)
    app.exec_()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
