"""一把出全部界面截图（离屏渲染，不需要真开窗口）。

用途：改完界面先看图上有没有越界、压扁、文字溢出，再上真机。
在临时库里塞一整套示例数据（项目、优先级、截止日、今日/累计工时、
一条正在计时），保证每次截图的条件一致，方便前后对比。

    python tools/preview_screens.py            # 输出到 docs/
    python tools/preview_screens.py D:\\tmp     # 指定输出目录
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# ⚠️ 不要默认 offscreen：本机的 offscreen 平台没有字体引擎，
# 截出来所有文字都是空白（实测），必须走真实窗口再 grab。
if os.getenv("ORBYTE_PREVIEW_OFFSCREEN") == "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ORBYTE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="orbyte-preview-")) / "p.db")

from PyQt5.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte import todo_store  # noqa: E402
from orbyte.db import database  # noqa: E402


def _stamp(hours_ago: float = 0, days_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago, days=days_ago)
    return moment.astimezone().isoformat(timespec="seconds")


def _seed() -> dict[str, str]:
    database.init_schema()
    for project in ("权限重构", "日常事务", "团队分析"):
        todo_store.add_project(project)

    plan = [
        ("联调权限模块 12 个接口", "权限重构", "P1", 0, 1, 9000, 7200),
        ("写接口文档", "权限重构", "", 2, 0, 1800, 1800),
        ("补旧客户端兼容层", "权限重构", "P2", 1, 0, 0, 1200),
        ("灰度上线方案评审", "权限重构", "P0", 5, 0, 0, 0),
        ("报销 10 月差旅", "日常事务", "P2", 0, 0, 0, 0),
        ("约运维过一遍备份策略", "日常事务", "P3", 3, 0, 0, 0),
        ("整理三季度项目复盘", "团队分析", "P1", 4, 0, 3600, 3000),
    ]
    ids: dict[str, str] = {}
    today = todo_store.today_str()
    for title, project, priority, due_offset, state, spent, today_sec in plan:
        item = todo_store.add(title, project, priority=priority)
        if due_offset is not None:
            due = (datetime.now() + timedelta(days=due_offset)).strftime("%Y-%m-%d")
            todo_store.update(item["uuid"], due_date=due)
        ids[title] = item["uuid"]
        with database.transaction() as conn:
            conn.execute(
                "UPDATE todo SET spent_sec = ? WHERE uuid = ?", (spent, item["uuid"])
            )
            if today_sec:
                conn.execute(
                    "INSERT INTO work_session (uuid, started_at, ended_at, sec)"
                    " VALUES (?, ?, ?, ?)",
                    (item["uuid"], f"{today}T09:00:00", f"{today}T10:00:00", today_sec),
                )
        if state == 1:
            todo_store.set_state(item["uuid"], "done")
        elif state == 2:
            todo_store.set_state(item["uuid"], "doing")

    # 一条正在计时：开始于 12 分 41 秒前，行内和底部都能看到在跑
    running = ids["灰度上线方案评审"]
    started = datetime.now(timezone.utc) - timedelta(seconds=761)
    with database.transaction() as conn:
        conn.execute("DELETE FROM active_session")
        conn.execute(
            "INSERT INTO active_session (uuid, started_at) VALUES (?, ?)",
            (running, started.astimezone().isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE todo SET state = 'doing' WHERE uuid = ?", (running,))

    life = [
        ("预约牙科", "P1"),
        ("换轮胎", ""),
        ("给猫买粮", "P3"),
        ("取快递", ""),
    ]
    for title, priority in life:
        item = todo_store.add(title, "", space=todo_store.SPACE_LIFE, priority=priority)
        if title == "换轮胎":
            todo_store.set_state(item["uuid"], "done")
    return ids


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screens"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    print("qt ready", flush=True)

    from orbyte.ui import theme

    theme.apply(theme.MODE_LIGHT)

    ids = _seed()
    print(f"seed ok: {len(ids)} 条", flush=True)

    from orbyte.ui.orb import Orb
    from orbyte.ui.widget import Widget
    from orbyte.ui.window import MainWindow

    def settle() -> None:
        """等一拍：QSS 应用、布局收敛、呼吸点动画起帧都在第一帧之后。"""
        loop = QEventLoop()
        QTimer.singleShot(600, loop.quit)
        loop.exec_()

    def shoot(target, name: str) -> None:
        target.show()
        target.raise_()
        settle()
        path = out_dir / name
        target.grab().save(str(path))
        print(f"{name}: {target.width()}x{target.height()} -> {path}")

    def compose(left, right, name: str, gap: int = 16, pad: int = 26, dark: bool = False) -> None:
        """把「面板 + 悬浮球」拼成一张图，模拟桌面上的真实相对位置。"""
        from PyQt5.QtGui import QColor, QPainter, QPixmap

        left.show()
        right.show()
        settle()
        lp = left.grab()
        rp = right.grab()
        height = max(lp.height(), rp.height()) + pad * 2
        width = lp.width() + gap + rp.width() + pad * 2
        canvas = QPixmap(width, height)
        canvas.fill(QColor("#0E1116" if dark else "#EDEAE5"))  # 桌面底色
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawPixmap(pad, (height - lp.height()) // 2, lp)
        painter.drawPixmap(pad + lp.width() + gap, (height - rp.height()) // 2, rp)
        painter.end()
        path = out_dir / name
        canvas.save(str(path))
        print(f"{name}: {width}x{height} -> {path}")

    def _shoot_done(panel, name: str) -> None:
        """把最后一行切成完成态，截一张「灰圈 + 整行删除线」。

        完成态只存在 0.7 秒，正常截图截不到，所以手动调一次 apply_state。
        """
        box = panel._rows_box
        row = box.itemAt(box.count() - 1).widget() if box.count() else None
        if row is None:
            return
        row.apply_state("done")
        settle()
        path = out_dir / name
        panel.grab().save(str(path))
        print(f"{name}: {panel.width()}x{panel.height()} -> {path}")
        panel.refresh()

    widget = Widget()
    widget.set_sync_status({"state": "idle", "pending": 3})

    # ---- 浅色 ----
    theme.apply(theme.MODE_LIGHT)
    widget.refresh()
    shoot(widget, "widget-light.png")

    # 状态菜单在第三版删掉了（点圈循环三态），改成截一张"完成态"：
    # 灰圈 + 整行删除线，停留 0.7s 后会从列表里移出
    widget._toggle_life()
    shoot(widget, "widget-life-light.png")
    widget._toggle_life()

    _shoot_done(widget, "widget-done-light.png")

    widget._settings.set_state(theme.MODE_LIGHT, False, False)
    widget._settings.show()
    settle()
    widget._settings.grab().save(str(out_dir / "widget-settings-light.png"))
    print("widget-settings-light.png ok")
    widget._settings.hide()

    # 挂件不再内嵌详情（360 宽塞不下表单），点任务名会开主窗口，详情图在窗口部分

    # ---- 深色 ----
    theme.apply(theme.MODE_DARK)
    widget.refresh()
    shoot(widget, "widget-dark.png")

    widget._toggle_life()
    shoot(widget, "widget-life-dark.png")
    widget._toggle_life()

    _shoot_done(widget, "widget-done-dark.png")

    # ---- 悬浮球与桌面形态 ----
    orb = Orb()
    orb.set_sync_state("idle")
    theme.apply(theme.MODE_LIGHT)
    widget.refresh()
    shoot(orb, "orb.png")
    compose(widget, orb, "widget-with-orb.png")
    theme.apply(theme.MODE_DARK)
    widget.refresh()
    compose(widget, orb, "widget-with-orb-dark.png", dark=True)
    orb.hide()

    # ---- 主窗口（跟随主题，两个都留一张）----
    window = MainWindow()
    window.set_sync_status({"state": "idle", "pending": 3})
    theme.apply(theme.MODE_LIGHT)
    window.refresh()
    shoot(window, "window-light.png")

    theme.apply(theme.MODE_DARK)
    window.refresh()
    shoot(window, "window-dark.png")

    window.show_detail(ids["灰度上线方案评审"])
    shoot(window, "window-detail-dark.png")
    window.show_list()
    window.hide()

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
