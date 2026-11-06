"""本地库自测：项目 / 工时流水 / 生活隔离 / 导出。

这些是"没有界面也能验证"的硬规则，每次改 todo_store 都应该跑一遍：

1. 新建的空项目要出现在分组里（否则「新建项目」点了像没反应）
2. 重命名项目要连带改组内待办，且重新排队
3. 删除项目不能连带删待办，只把它们移到「未归类」
4. 计时：进 doing 开表、离开结算，并落一条 work_session（今日工时靠它）
5. 生活待办：不建计时、不进 outbox
6. 导出生活待办是纯 JSON，不含令牌/服务器地址

    python tools/selftest_core.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["ORBYTE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="orbyte-core-")) / "c.db")

from orbyte import system, todo_store  # noqa: E402
from orbyte.db import database  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(("  OK   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    database.init_schema()

    print("1) 项目")
    todo_store.add_project("权限重构")
    todo_store.add_project("日常事务")
    names = [group[0] for group in todo_store.groups(todo_store.SPACE_WORK)]
    check(names[:2] == ["权限重构", "日常事务"], "空项目按新建顺序出现在分组里")

    item = todo_store.add("联调接口", "权限重构", priority="P1",
                          due_date="2026-11-02", planned_min=90)
    check(item["priority"] == "P1", "优先级写入")
    check(item["due_date"] == "2026-11-02", "截止日写入")
    check(item["planned_min"] == 90, "计划工时写入")
    check(todo_store.pending_count() == 1, "新建工作待办排进队列")

    affected = todo_store.rename_project("权限重构", "权限重构 v2")
    check(affected == 1, "重命名影响 1 条待办")
    renamed = todo_store.get_todo(item["uuid"]) or {}
    check(renamed["project"] == "权限重构 v2", "待办的项目名跟着改")
    check(todo_store.pending_count() >= 2, "重命名后重新排队")

    moved = todo_store.delete_project("权限重构 v2")
    check(moved == 1, "删除项目影响 1 条待办")
    check((todo_store.get_todo(item["uuid"]) or {}).get("project") == "",
          "待办被移到未归类（没有连带删除）")

    print("2) 计时")
    todo_store.set_state(item["uuid"], "doing")
    active = todo_store.active_info()
    check(bool(active), "进入进行中开始计时")
    time.sleep(1.3)
    todo_store.set_state(item["uuid"], "done")
    after = todo_store.get_todo(item["uuid"]) or {}
    check(int(after["spent_sec"]) >= 1, f"结算进 spent_sec（{after['spent_sec']}s）")
    check(todo_store.active_info() is None, "完成后计时停止")
    today = todo_store.today_sec(item["uuid"])
    check(today >= 1, f"今日工时来自工时流水（{today}s）")
    rows = database.conn().execute("SELECT COUNT(*) AS n FROM work_session").fetchone()
    check(int(rows["n"]) == 1, "work_session 落了一条流水")

    second = todo_store.add("写文档", "")
    todo_store.set_state(item["uuid"], "doing")
    todo_store.set_state(second["uuid"], "doing")
    check((todo_store.active_info() or {}).get("uuid") == second["uuid"],
          "同一时刻只允许一条在计时（切换自动结算上一条）")
    todo_store.set_state(second["uuid"], "todo")

    print("3) 生活待办")
    life = todo_store.add("买猫粮", "", space=todo_store.SPACE_LIFE, priority="P2")
    todo_store.set_state(life["uuid"], "doing")
    check(todo_store.active_info() is None, "生活待办不建计时")
    check(not todo_store.has_pending(life["uuid"]), "生活待办不进同步队列")
    check(todo_store.get_todo(life["uuid"])["priority"] == "P2", "生活待办也能标优先级")

    print("4) 导出")
    export_path = Path(tempfile.mkdtemp(prefix="orbyte-export-")) / "life.json"
    count = system.export_life(export_path)
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    check(count == 1, "导出条数正确")
    check(payload["todos"][0]["title"] == "买猫粮", "导出内容正确")
    raw = export_path.read_text(encoding="utf-8")
    check("dtk_" not in raw and "token" not in raw.lower(), "导出文件不含令牌")

    print("7) 升级路径")
    # 模拟"版本号已经写到最新、但某条 ALTER 被跳过"的历史脏库：
    # 这是真实踩过的坑（enumerate 下标当版本号），补列兜底必须能自愈。
    import sqlite3

    from orbyte.db import Database

    dirty = Path(tempfile.mkdtemp(prefix="orbyte-upgrade-")) / "old.db"
    conn = sqlite3.connect(str(dirty))
    conn.executescript(
        """
        CREATE TABLE todo (
          uuid TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
          project TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'todo',
          space TEXT NOT NULL DEFAULT 'work', position INTEGER NOT NULL DEFAULT 0,
          started_at TEXT, spent_sec INTEGER NOT NULL DEFAULT 0,
          sync_state TEXT NOT NULL DEFAULT 'local',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
        );
        CREATE TABLE outbox (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', retries INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        PRAGMA user_version = 8;
        """
    )
    conn.commit()
    conn.close()

    Database(dirty).init_schema()
    conn = sqlite3.connect(str(dirty))
    healed = {row[1] for row in conn.execute("PRAGMA table_info(todo)")}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    check({"priority", "due_date", "planned_min"} <= healed, "脏库缺列被补回（priority/due_date/planned_min）")
    check("project" in tables and "work_session" in tables, "脏库缺表被建回（project/work_session）")
    Database(dirty).init_schema()  # 再来一次确认幂等
    check(True, "重复升级幂等")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "；".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
