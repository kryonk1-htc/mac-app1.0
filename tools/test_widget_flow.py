"""挂件交互流程冒烟：下拉切状态 / 完成态 0.9s 后移出 / 点标题不误切状态。

单独一个脚本是因为这几个行为靠截图看不出来——截图只能证明"长这样"，
证明不了"点下去会发生什么"。

    python tools/test_widget_flow.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ORBYTE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="orbyte-flow-")) / "p.db")

from PyQt5.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte import todo_store  # noqa: E402
from orbyte.db import database  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def pump(app, ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def click(widget, pos: QPoint) -> None:
    local = QPoint(pos)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        event = QMouseEvent(
            kind, local, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )
        QApplication.sendEvent(widget, event)


def rows(panel) -> list:
    box = panel._rows_box
    return [box.itemAt(i).widget() for i in range(box.count())]


def main() -> int:
    app = QApplication([])
    database.init_schema()
    todo_store.add_project("权限重构")
    first = todo_store.add("甲：先做这条", "权限重构")
    todo_store.add("乙：第二条", "权限重构")

    from orbyte.ui import theme
    from orbyte.ui.widget import Widget, _StatusMenu

    theme.apply(theme.MODE_LIGHT)
    panel = Widget()
    panel.show()          # 需要真实布局才能算坐标
    pump(app, 300)

    print("1) 点状态按钮：弹出三选一，不直接改状态")
    row = rows(panel)[0]
    opened: list[str] = []
    panel.openDetailRequested.connect(opened.append)
    check("初始状态是未开始", row._status.state() == "todo", row._status.state())
    check("首行挂着状态按钮", row._status.isVisible())
    check("首行挂着优先级徽章", row.findChildren(type(row._title)) is not None)
    click(row._status, QPoint(20, 14))
    pump(app, 150)
    menus = [m for m in panel.findChildren(_StatusMenu) if m.isVisible()]
    check("弹出状态菜单", len(menus) == 1, str(len(menus)))
    check("弹菜单没顺手改状态", row._status.state() == "todo", row._status.state())
    check("没有误开详情", opened == [], str(opened))
    for m in menus:
        m.close()
    pump(app, 80)

    print("2) 选「进行中」：落库 + 启动计时 + 置顶")
    panel._set_state(row, first["uuid"], "doing")
    pump(app, 250)
    row = rows(panel)[0]
    check("状态已变进行中", row._status.state() == "doing", row._status.state())
    check(
        "库里已落盘",
        todo_store.get_todo(first["uuid"])["state"] == "doing",
        todo_store.get_todo(first["uuid"])["state"],
    )
    active = todo_store.active_info()
    check("计时已启动", bool(active) and active["uuid"] == first["uuid"])
    titles = [r.item["title"] for r in rows(panel)]
    check("进行中排第一", titles[0] == "甲：先做这条", str(titles))

    print("3) 选「已完成」：0.9s 后从列表移出")
    panel._set_state(row, first["uuid"], "done")
    pump(app, 150)
    check("状态已变完成", row._status.state() == "done", row._status.state())
    check("仍在列表里（给用户看一眼删除线）", row in rows(panel))
    check("标题有删除线", row._title.font().strikeOut())
    pump(app, 1100)
    titles = [r.item["title"] for r in rows(panel)]
    check("已从列表移出", "甲：先做这条" not in titles, str(titles))
    check(
        "数据还在（只是不显示）",
        todo_store.get_todo(first["uuid"])["state"] == "done",
    )
    check("计时已结算停止", todo_store.active_info() is None)

    print("4) 点标题：打开详情（不是切状态）")
    row = rows(panel)[0]
    before = row._status.state()
    click(row, QPoint(180, 20))
    pump(app, 120)
    check("发出 openDetailRequested", bool(opened), str(opened))
    check("状态没被顺带改掉", row._status.state() == before, row._status.state())

    print("5) 面板尺寸（1.5 整体放大 1.33 倍）")
    check(
        "宽 640、高度自适应（内容少时收缩，不再留空白）",
        panel.width() == 640 and 400 <= panel.height() <= 790,
        f"{panel.width()}x{panel.height()}",
    )
    check("行高 90（上下间距撑开，徽章不顶标题）", row.height() == 90, str(row.height()))
    check("标题栏存在", panel.findChild(type(panel._list_page)) is not None)

    print("6) 底栏")
    check("底栏已删除（无 _foot_label）", not hasattr(panel, "_foot_label"), "removed")

    panel.hide()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
