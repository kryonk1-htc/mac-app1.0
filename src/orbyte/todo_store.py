"""待办读写（本地主库）+ 生活待办隔离的第一道闸。

三条不变量，改这个文件时必须同时满足：

1. `space='life'` 的记录**连排队的资格都没有**：`_enqueue()` 里直接 return。
   这是三重保险的第一道，处理在写入层，最靠前、最不容易被绕过。
2. 业务表与 outbox 同一事务：`with database.transaction()` 包住，
   中间抛任何异常都整体回滚，不会留下"改了但没排队"的幽灵数据。
3. 工时按"切换时刻"结算：进入 doing 开表、离开 doing 结账并落一条
   work_session 流水（"今天 2h"要靠流水算，只存 spent_sec 算不出来），
   崩溃后靠 active_session 表恢复计时，不会因为强杀而丢工时。
"""

from __future__ import annotations

import json
import sqlite3
import uuid as uuidlib
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import database

STATE_TODO = "todo"
STATE_DOING = "doing"
STATE_DONE = "done"
STATES = (STATE_TODO, STATE_DOING, STATE_DONE)

SPACE_WORK = "work"
SPACE_LIFE = "life"

#: 没填项目时的分组名（工作区必填，但历史数据/手快留空要有个去处）
UNCATEGORIZED = "未归类"

#: 优先级从高到低；空串 = 未设置（2026-11-04 起只有 P0/P1/P2）
PRIORITIES = ("P0", "P1", "P2")


def now_iso() -> str:
    """统一时间格式：带时区的 ISO8601（本地与服务器比较时不会歧义）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    """本地日期 YYYY-MM-DD：截止日与本日工时都按本地日历，不按 UTC。"""
    return datetime.now().strftime("%Y-%m-%d")


def new_uuid() -> str:
    """客户端生成的稳定标识——重装、换机后仍能对上同一条。"""
    return uuidlib.uuid4().hex


def _display_project(value: str | None) -> str:
    return (value or "").strip()


# ---- 内部：排队 ----

def _enqueue(conn: sqlite3.Connection, item_uuid: str, space: str) -> None:
    """把一条改动排进待发队列。

    ⚠️ 第一道拦截就在这里：生活待办不进队列。
    不是"发的时候过滤"，而是根本不存在可发的东西。
    """
    if space != SPACE_WORK:
        return
    conn.execute(
        "INSERT INTO outbox (uuid, state, created_at) VALUES (?, 'pending', ?)",
        (item_uuid, now_iso()),
    )


def _close_session(conn: sqlite3.Connection, at_iso: str | None = None) -> None:
    """结束当前计时，把秒数加回 spent_sec，并落一条工时流水。"""
    row = conn.execute("SELECT uuid, started_at FROM active_session LIMIT 1").fetchone()
    if not row:
        return
    now = datetime.fromisoformat(at_iso) if at_iso else datetime.now(timezone.utc)
    started_iso = row["started_at"]
    try:
        started = datetime.fromisoformat(started_iso)
    except ValueError:
        started = now
    elapsed = max(0, int((now - started).total_seconds()))
    if elapsed:
        stamp = now_iso()
        conn.execute(
            "UPDATE todo SET spent_sec = spent_sec + ?, updated_at = ? "
            "WHERE uuid = ? AND deleted_at IS NULL",
            (elapsed, stamp, row["uuid"]),
        )
        # 「今天 2h」只能靠流水算：spent_sec 是个总数，看不出是哪天花的
        conn.execute(
            "INSERT INTO work_session (uuid, started_at, ended_at, sec) "
            "VALUES (?, ?, ?, ?)",
            (row["uuid"], started_iso, now.isoformat(timespec="seconds"), elapsed),
        )
        target = conn.execute(
            "SELECT space FROM todo WHERE uuid = ?", (row["uuid"],)
        ).fetchone()
        if target and target["space"] == SPACE_WORK:
            _enqueue(conn, row["uuid"], SPACE_WORK)
    conn.execute("DELETE FROM active_session")


# ---- 查询 ----

def list_todos(
    space: str = SPACE_WORK, include_done: bool = True
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM todo WHERE space = ? AND deleted_at IS NULL"
    if not include_done:
        sql += " AND state != 'done'"
    sql += " ORDER BY position ASC, created_at ASC"
    rows = database.conn().execute(sql, (space,)).fetchall()
    return [dict(row) for row in rows]


def get_todo(item_uuid: str) -> dict[str, Any] | None:
    row = database.conn().execute(
        "SELECT * FROM todo WHERE uuid = ?", (item_uuid,)
    ).fetchone()
    return dict(row) if row else None


def counts(space: str = SPACE_WORK) -> dict[str, int]:
    rows = database.conn().execute(
        "SELECT state, COUNT(*) AS n FROM todo "
        "WHERE space = ? AND deleted_at IS NULL GROUP BY state",
        (space,),
    ).fetchall()
    result = {STATE_TODO: 0, STATE_DOING: 0, STATE_DONE: 0}
    for row in rows:
        result[row["state"]] = row["n"]
    return result


def remaining(space: str = SPACE_WORK) -> int:
    """挂件右上角的「余 N 条」：已完成的当然不算。"""
    row = database.conn().execute(
        "SELECT COUNT(*) AS n FROM todo WHERE space = ? AND deleted_at IS NULL "
        "AND state != 'done'",
        (space,),
    ).fetchone()
    return int(row["n"]) if row else 0


def active_uuid() -> str | None:
    row = database.conn().execute("SELECT uuid FROM active_session LIMIT 1").fetchone()
    return row["uuid"] if row else None


def active_info() -> dict[str, Any] | None:
    """当前计时的那一条（含标题与开始时刻），没有则 None。

    面板底部"计时中 · 标题 · 00:12:41"用它，托盘也用它——
    计时类工具必须能自证在跑，否则用户永远怀疑"它是不是停了"。
    """
    row = database.conn().execute(
        "SELECT a.uuid, a.started_at, t.title, t.project, t.space, t.spent_sec "
        "FROM active_session a JOIN todo t ON t.uuid = a.uuid "
        "WHERE t.deleted_at IS NULL LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def today_sec(item_uuid: str) -> int:
    """今天在这条上花掉的秒数（跨零点按流水切分，不按整天猜）。"""
    row = database.conn().execute(
        "SELECT COALESCE(SUM(sec), 0) AS s FROM work_session "
        "WHERE uuid = ? AND substr(started_at, 1, 10) = ?",
        (item_uuid, today_str()),
    ).fetchone()
    return int(row["s"]) if row else 0


def today_total_sec(space: str = SPACE_WORK) -> int:
    row = database.conn().execute(
        "SELECT COALESCE(SUM(w.sec), 0) AS s FROM work_session w "
        "JOIN todo t ON t.uuid = w.uuid "
        "WHERE t.space = ? AND substr(w.started_at, 1, 10) = ?",
        (space, today_str()),
    ).fetchone()
    return int(row["s"]) if row else 0


def groups(space: str = SPACE_WORK) -> list[tuple[str, list[dict[str, Any]]]]:
    """按项目分组（项目表里没有待办的空组也会出现）。

    组序：先按 project.sort（用户新建项目的顺序），再按组内首条的 position。
    这样「新建项目」能立刻出现在列表里，而不是等第一条待办才冒出来。
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in list_todos(space):
        name = _display_project(item.get("project"))
        buckets.setdefault(name, []).append(item)

    ordered: list[tuple[str, list[dict[str, Any]]]] = []
    known: list[str] = []
    if space == SPACE_WORK:
        rows = database.conn().execute(
            "SELECT name FROM project WHERE deleted_at IS NULL ORDER BY sort ASC, created_at ASC"
        ).fetchall()
        known = [_display_project(row["name"]) for row in rows]

    for name in known:
        items = buckets.pop(name, None)
        ordered.append((name, items or []))

    # 项目表里没登记的（历史数据 / 直接写字符串）按首条 position 排，
    # 未归类永远放最后
    rest = sorted(
        buckets.items(),
        key=lambda pair: (
            1 if pair[0] == "" else 0,
            int(pair[1][0].get("position") or 0) if pair[1] else 0,
        ),
    )
    for name, items in rest:
        ordered.append((name, items))
    return ordered


def projects() -> list[dict[str, Any]]:
    """项目 + 统计（主窗口的进度条与组工时用）。"""
    result: list[dict[str, Any]] = []
    for name, items in groups(SPACE_WORK):
        total = len(items)
        done = sum(1 for item in items if item["state"] == STATE_DONE)
        spent = sum(int(item.get("spent_sec") or 0) for item in items)
        result.append(
            {
                "name": name,
                "label": name or UNCATEGORIZED,
                "total": total,
                "done": done,
                "spent_sec": spent,
                "items": items,
            }
        )
    return result


# ---- 写入 ----

def add(
    title: str,
    project: str,
    space: str = SPACE_WORK,
    position: int | None = None,
    priority: str = "",
    due_date: str | None = None,
    planned_min: int = 0,
) -> dict[str, Any]:
    """新增一条待办（工作待办会自动排进同步队列）。"""
    item_uuid = new_uuid()
    stamp = now_iso()
    clean_project = project.strip()
    with database.transaction() as conn:
        if position is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM todo WHERE space = ?",
                (space,),
            ).fetchone()
            position = int(row["p"])
        # 往新项目里加第一条时，顺手把它登记进项目表，否则组序会掉到最后
        if space == SPACE_WORK and clean_project:
            _ensure_project(conn, clean_project)
        conn.execute(
            "INSERT INTO todo (uuid, title, project, space, position, priority,"
            " due_date, planned_min, sync_state, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_uuid,
                title.strip(),
                clean_project,
                space,
                position,
                priority if priority in PRIORITIES else "",
                due_date or None,
                max(0, int(planned_min or 0)),
                "pending" if space == SPACE_WORK else "local",
                stamp,
                stamp,
            ),
        )
        if space == SPACE_WORK:
            _enqueue(conn, item_uuid, space)
    return get_todo(item_uuid) or {}


def update(item_uuid: str, **fields: Any) -> dict[str, Any] | None:
    """改标题/项目/优先级/截止日/计划工时/排序。

    白名单是有意为之：uuid、spent_sec、sync_state 只能由内部逻辑改，
    开放给调用方迟早出现"工时被界面重置成 0"这种事。
    """
    allowed = {"title", "project", "position", "priority", "due_date", "planned_min"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return get_todo(item_uuid)
    if "priority" in patch and patch["priority"] not in PRIORITIES:
        patch["priority"] = ""
    if "planned_min" in patch:
        patch["planned_min"] = max(0, int(patch["planned_min"] or 0))
    if "due_date" in patch:
        patch["due_date"] = patch["due_date"] or None
    row = get_todo(item_uuid)
    if not row:
        return None
    stamp = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in patch)
    values = list(patch.values())
    with database.transaction() as conn:
        if (
            "project" in patch
            and row["space"] == SPACE_WORK
            and _display_project(patch["project"])
        ):
            _ensure_project(conn, _display_project(patch["project"]))
        conn.execute(
            f"UPDATE todo SET {assignments}, updated_at = ?, "
            f"sync_state = CASE WHEN space = '{SPACE_WORK}' THEN 'pending' ELSE sync_state END "
            "WHERE uuid = ?",
            (*values, stamp, item_uuid),
        )
        _enqueue(conn, item_uuid, row["space"])
    return get_todo(item_uuid)


def set_state(item_uuid: str, state: str) -> dict[str, Any] | None:
    """切换红/黄/绿三态，并顺带结算工时。

    规则：进入 doing 开始计时；离开 doing 结算到 spent_sec + 记一条流水。
    同一时刻全局只允许一条 doing，切换时自动把上一条停下。
    """
    if state not in STATES:
        raise ValueError(f"未知状态: {state}")
    row = get_todo(item_uuid)
    if not row:
        return None

    stamp = now_iso()
    # 生活待办不计工时（口径：工时只算工作项），所以只在工作区动 active_session
    timed = row["space"] == SPACE_WORK
    with database.transaction() as conn:
        if state != STATE_DOING and timed:
            current = conn.execute(
                "SELECT uuid FROM active_session WHERE uuid = ?", (item_uuid,)
            ).fetchone()
            if current:
                _close_session(conn)
        if state == STATE_DOING and timed:
            # 全局唯一：先把别人的计时结掉
            _close_session(conn)
            conn.execute(
                "INSERT INTO active_session (uuid, started_at) VALUES (?, ?) "
                "ON CONFLICT(uuid) DO UPDATE SET started_at = excluded.started_at",
                (item_uuid, stamp),
            )
        conn.execute(
            "UPDATE todo SET state = ?, updated_at = ?, "
            "sync_state = CASE WHEN space = 'work' THEN 'pending' ELSE sync_state END "
            "WHERE uuid = ?",
            (state, stamp, item_uuid),
        )
        _enqueue(conn, item_uuid, row["space"])
    return get_todo(item_uuid)


def delete(item_uuid: str) -> bool:
    """软删除：本地不显示，同时把"删除"这个事实同步给服务器。"""
    row = get_todo(item_uuid)
    if not row:
        return False
    stamp = now_iso()
    with database.transaction() as conn:
        conn.execute(
            "UPDATE todo SET deleted_at = ?, updated_at = ?, "
            "sync_state = CASE WHEN space = 'work' THEN 'pending' ELSE sync_state END "
            "WHERE uuid = ?",
            (stamp, stamp, item_uuid),
        )
        conn.execute("DELETE FROM active_session WHERE uuid = ?", (item_uuid,))
        _enqueue(conn, item_uuid, row["space"])
    return True


# ---- 项目（本地表：服务端只认待办上的 project 字符串）----

def _ensure_project(conn: sqlite3.Connection, name: str) -> None:
    row = conn.execute("SELECT name FROM project WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute("UPDATE project SET deleted_at = NULL WHERE name = ?", (name,))
        return
    nxt = conn.execute(
        "SELECT COALESCE(MAX(sort), 0) + 1 AS s FROM project"
    ).fetchone()
    conn.execute(
        "INSERT INTO project (name, sort, created_at) VALUES (?, ?, ?)",
        (name, int(nxt["s"]), now_iso()),
    )


def add_project(name: str) -> str | None:
    """新建一个空项目：立刻出现在列表里，可以往里添加待办。"""
    clean = name.strip()
    if not clean:
        return None
    with database.transaction() as conn:
        _ensure_project(conn, clean)
    return clean


def rename_project(old: str, new: str) -> int:
    """重命名项目：同时改掉组内所有待办，返回受影响的条数。"""
    clean_old = old.strip()
    clean_new = new.strip()
    if not clean_new or clean_old == clean_new:
        return 0
    stamp = now_iso()
    affected = 0
    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT uuid FROM todo WHERE space = ? AND project = ? AND deleted_at IS NULL",
            (SPACE_WORK, clean_old),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE todo SET project = ?, updated_at = ?, sync_state = 'pending' "
                "WHERE uuid = ?",
                (clean_new, stamp, row["uuid"]),
            )
            _enqueue(conn, row["uuid"], SPACE_WORK)
            affected += 1
        conn.execute("DELETE FROM project WHERE name = ?", (clean_old,))
        _ensure_project(conn, clean_new)
    return affected


def delete_project(name: str) -> int:
    """删除项目：组内待办移到「未归类」，不连带删数据。

    直接连条删掉是最容易「手一滑丢一周记录」的做法，成本远大于收益；
    真要清空，用户可以逐条删，至少每一步都可见。
    """
    clean = name.strip()
    stamp = now_iso()
    affected = 0
    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT uuid FROM todo WHERE space = ? AND project = ? AND deleted_at IS NULL",
            (SPACE_WORK, clean),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE todo SET project = '', updated_at = ?, sync_state = 'pending' "
                "WHERE uuid = ?",
                (stamp, row["uuid"]),
            )
            _enqueue(conn, row["uuid"], SPACE_WORK)
            affected += 1
        conn.execute(
            "UPDATE project SET deleted_at = ? WHERE name = ?", (stamp, clean)
        )
    return affected


# ---- 给同步用 ----


def pending_count() -> int:
    row = database.conn().execute(
        "SELECT COUNT(*) AS n FROM outbox WHERE state = 'pending'"
    ).fetchone()
    return int(row["n"]) if row else 0


def fetch_pending(limit: int = 100) -> list[dict[str, Any]]:
    """取待发条目。

    同一个 uuid 可能有多条 outbox（连续编辑了三次），这里只取**最新那条**：
    推中间态没有任何意义，反而会让服务端按到达顺序覆盖成旧内容。
    """
    rows = database.conn().execute(
        "SELECT o.seq, o.uuid, t.title, t.project, t.state, t.space, t.position,"
        " t.started_at, t.spent_sec, t.priority, t.due_date, t.planned_min,"
        " t.updated_at, t.deleted_at, t.log_id "
        "FROM outbox o "
        "JOIN todo t ON t.uuid = o.uuid "
        "WHERE o.state = 'pending' AND t.space = 'work' "
        "  AND o.seq = (SELECT MAX(seq) FROM outbox WHERE uuid = o.uuid AND state = 'pending') "
        "ORDER BY o.seq ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_inflight(uuids: Iterable[str]) -> None:
    """把要发的待办标成「已发出等确认」。

    ⚠️ 按 uuid 标、不是按 seq：同一条可能连改三次留下三行 outbox，
    只标最新那行会让旧行永远留在 pending，下一轮又发一次同样的数据。
    这是实测出来的重复推送来源（自测里表现为"3 条推了 4 次"）。
    """
    uuids = list(dict.fromkeys(uuids))
    if not uuids:
        return
    with database.transaction() as conn:
        conn.executemany(
            "UPDATE outbox SET state = 'inflight' WHERE uuid = ? AND state = 'pending'",
            [(uuid,) for uuid in uuids],
        )


def ack_upto(upto_seq: int) -> int:
    """确认 seq <= upto 的全部条目；更新的继续留在 inflight 等下一轮。

    注意：调用方保证 upto_seq 之前的都成功了。宁可重发也不能漏发，
    所以这里只"确认已经确认过的"，不猜。
    """
    with database.transaction() as conn:
        cursor = conn.execute(
            "UPDATE outbox SET state = 'acked' WHERE state = 'inflight' AND seq <= ?",
            (upto_seq,),
        )
        conn.execute(
            "UPDATE todo SET sync_state = 'synced' "
            "WHERE sync_state = 'pending' AND uuid NOT IN "
            "(SELECT uuid FROM outbox WHERE state = 'pending')"
        )
        return cursor.rowcount


def reset_inflight() -> int:
    """进程启动/崩溃恢复：把卡在 inflight 的条目退回 pending。

    数据只在内存里"已发出"，服务端没确认就等于没发。不重置的话
    这些条目会永远卡住，直到用户再改一次——那是最难排查的漏传。
    """
    with database.transaction() as conn:
        cursor = conn.execute(
            "UPDATE outbox SET state = 'pending' WHERE state = 'inflight'"
        )
        return cursor.rowcount


# ---- 拉取（服务端 → 本地）----


def has_pending(item_uuid: str) -> bool:
    row = database.conn().execute(
        "SELECT COUNT(*) AS n FROM outbox WHERE uuid = ? AND state IN ('pending','inflight')",
        (item_uuid,),
    ).fetchone()
    return bool(row and row["n"])


def apply_remote(items: list[dict[str, Any]]) -> dict[str, int]:
    """把拉下来的记录并进本地，返回 {新增, 更新, 跳过} 计数。

    冲突口径：本地有未推送的改动，且本地 updated_at 更新 → 保留本地
    （下一次推送会把服务端覆盖掉）；否则服务端为准。远端删除同样落成软删除。
    合并进来的行**不排进 outbox**，否则会形成"拉下来又推回去"的回声。
    """
    added = updated = skipped = 0
    with database.transaction() as conn:
        for item in items:
            item_uuid = str(item.get("uuid") or "").strip()
            if not item_uuid:
                continue
            remote_updated = str(item.get("updated_at") or "")
            row = conn.execute(
                "SELECT uuid, updated_at, space FROM todo WHERE uuid = ?", (item_uuid,)
            ).fetchone()
            if row:
                if row["space"] == SPACE_LIFE:
                    skipped += 1
                    continue
                if has_pending(item_uuid) and row["updated_at"] >= remote_updated:
                    skipped += 1
                    continue
                conn.execute(
                    "UPDATE todo SET title = ?, project = ?, state = ?, position = ?,"
                    " started_at = ?, spent_sec = ?, priority = ?, due_date = ?,"
                    " planned_min = ?, sync_state = 'synced', updated_at = ?,"
                    " deleted_at = ?,"
                    " log_id = COALESCE(?, log_id) WHERE uuid = ?",
                    (
                        item.get("title") or "",
                        item.get("project") or "",
                        item.get("state") if item.get("state") in STATES else STATE_TODO,
                        int(item.get("position") or 0),
                        item.get("started_at"),
                        int(item.get("spent_sec") or 0),
                        item.get("priority") or "",
                        item.get("due_date"),
                        int(item.get("planned_min") or 0),
                        remote_updated or now_iso(),
                        item.get("deleted_at"),
                        item.get("log_id"),
                        item_uuid,
                    ),
                )
                # 本地改动被远端覆盖：这些排队已经没意义，留着会推回旧内容
                conn.execute("DELETE FROM outbox WHERE uuid = ?", (item_uuid,))
                conn.execute("DELETE FROM active_session WHERE uuid = ?", (item_uuid,))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO todo (uuid, title, project, space, position, state,"
                    " started_at, spent_sec, priority, due_date, planned_min,"
                    " sync_state, created_at, updated_at, deleted_at, log_id)"
                    " VALUES (?, ?, ?, 'work', ?, ?, ?, ?, ?, ?, ?, 'synced', ?, ?, ?, ?)",
                    (
                        item_uuid,
                        item.get("title") or "",
                        item.get("project") or "",
                        int(item.get("position") or 0),
                        item.get("state") if item.get("state") in STATES else STATE_TODO,
                        item.get("started_at"),
                        int(item.get("spent_sec") or 0),
                        item.get("priority") or "",
                        item.get("due_date"),
                        int(item.get("planned_min") or 0),
                        remote_updated or now_iso(),
                        remote_updated or now_iso(),
                        item.get("deleted_at"),
                        item.get("log_id"),
                    ),
                )
                if item.get("project"):
                    _ensure_project(conn, str(item["project"]))
                added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


def life_export() -> list[dict[str, Any]]:
    """导出生活待办（换机/卸载前的兜底，只读）。"""
    rows = list_todos(SPACE_LIFE)
    keep = ("uuid", "title", "state", "spent_sec", "created_at", "updated_at")
    return [{key: row.get(key) for key in keep} for row in rows]


# ---- 应用快照镜像（双向同步的「应用 → 插件」半边）----


def apply_app_snapshot(projects: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, int]:
    """把应用侧的全量快照并进本地库，返回 {added, updated, removed, skipped}。

    身份映射：应用条目 id ↔ todo.log_id。插件自己的待办（log_id 为空）
    一律不碰——那是插件这边的创作，应用快照没有资格覆盖。

    冲突口径与 apply_remote 一致：本地有未推送的改动且 updated_at 更新
    → 保留本地（等推送把它带过去）；否则应用为准。

    删除口径：应用里消失的条目（且本地有 log_id、没有待发改动）→
    本地软删除。插件自己建的条目不受影响。
    """
    added = updated = removed = skipped = 0
    stamp = now_iso()
    with database.transaction() as conn:
        # 完整项目树（含层级）原样存进 meta：主页按树挂载条目，
        # 只有一级项目名表达不了「智能协作工作台/开发/王」这种深度。
        # 注意：此刻已在本模块的 transaction 里，不能调 database.set_meta
        # （它自己再开事务会重入非重入锁死锁），直接用当前 conn 写。
        conn.execute(
            "INSERT INTO meta (k, v) VALUES ('app_projects', ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (
                json.dumps(
                    [
                        {
                            "id": str(proj.get("id") or ""),
                            "name": str(proj.get("name") or ""),
                            "parent_id": proj.get("parent_id") or None,
                        }
                        for proj in projects
                        if str(proj.get("id") or "").strip()
                    ],
                    ensure_ascii=False,
                ),
            ),
        )

        # 应用的一级项目登记进本地项目表：输入框下拉、组序仍按名字走
        for proj in projects:
            name = str(proj.get("name") or "").strip()
            # 只登记一级项目（parent_id 为空）；子项目在应用里是层级，
            # 插件侧统一按一级项目名分组
            if name and not proj.get("parent_id"):
                row = conn.execute(
                    "SELECT name FROM project WHERE name = ?", (name,)
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE project SET deleted_at = NULL WHERE name = ?", (name,)
                    )
                else:
                    nxt = conn.execute(
                        "SELECT COALESCE(MAX(sort), 0) + 1 AS s FROM project"
                    ).fetchone()
                    conn.execute(
                        "INSERT INTO project (name, sort, created_at) VALUES (?, ?, ?)",
                        (name, int(nxt["s"]), stamp),
                    )

        seen_ids: set[str] = set()
        for item in items:
            log_id = str(item.get("id") or "").strip()
            if not log_id:
                continue
            seen_ids.add(log_id)
            title = str(item.get("title") or "").strip()
            if not title:
                skipped += 1
                continue
            state = item.get("state") if item.get("state") in STATES else STATE_TODO
            priority = str(item.get("priority") or "")
            if priority not in PRIORITIES:
                priority = ""
            project = str(item.get("top_project") or "").strip()
            app_project_id = str(item.get("project_id") or "").strip() or None
            app_detail = str(item.get("detail") or "").strip() or None
            remote_updated = str(item.get("updated_at") or "")

            row = conn.execute(
                "SELECT uuid, updated_at, space, state FROM todo WHERE log_id = ? "
                "AND deleted_at IS NULL LIMIT 1",
                (log_id,),
            ).fetchone()
            if row:
                if row["space"] == SPACE_LIFE:
                    skipped += 1
                    continue
                if has_pending(row["uuid"]) and row["updated_at"] >= remote_updated:
                    skipped += 1
                    continue
                conn.execute(
                    "UPDATE todo SET title = ?, project = ?, state = ?, priority = ?,"
                    " app_project_id = ?, app_detail = ?,"
                    " sync_state = CASE WHEN sync_state = 'local' THEN 'local' ELSE 'synced' END,"
                    " updated_at = ? WHERE uuid = ?",
                    (
                        title, project, state, priority, app_project_id, app_detail,
                        remote_updated or stamp, row["uuid"],
                    ),
                )
                if has_pending(row["uuid"]) and row["updated_at"] < remote_updated:
                    # 本地改动被应用覆盖：待发队列清掉，否则推回去又打架
                    conn.execute("DELETE FROM outbox WHERE uuid = ?", (row["uuid"],))
                updated += 1
            else:
                item_uuid = new_uuid()
                conn.execute(
                    "INSERT INTO todo (uuid, title, project, space, position, state,"
                    " priority, sync_state, created_at, updated_at, log_id,"
                    " app_project_id, app_detail)"
                    " VALUES (?, ?, ?, 'work', 0, ?, ?, 'synced', ?, ?, ?, ?, ?)",
                    (item_uuid, title, project, state, priority, stamp, stamp,
                     log_id, app_project_id, app_detail),
                )
                if project:
                    _ensure_project(conn, project)
                added += 1

        # 应用里消失的条目 → 本地软删除镜像（只动有 log_id 且无待发改动的行）
        mirror_rows = conn.execute(
            "SELECT uuid, log_id FROM todo WHERE log_id IS NOT NULL AND deleted_at IS NULL"
        ).fetchall()
        for row in mirror_rows:
            item_uuid = row["uuid"]
            if row["log_id"] in seen_ids:
                continue
            if has_pending(item_uuid):
                skipped += 1
                continue
            conn.execute(
                "UPDATE todo SET deleted_at = ?, updated_at = ? WHERE uuid = ?",
                (stamp, stamp, item_uuid),
            )
            conn.execute("DELETE FROM active_session WHERE uuid = ?", (item_uuid,))
            removed += 1
    return {"added": added, "updated": updated, "removed": removed, "skipped": skipped}


# ---- 应用项目树（主页树形视图的数据源）----


def app_projects() -> list[dict[str, Any]]:
    """上次快照里的完整项目树（扁平列表，parent_id 串层级）。

    没拉过快照时返回空列表——树形视图会回落到旧的按名分组展示。
    """
    raw = database.get_meta("app_projects", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [row for row in data if isinstance(row, dict) and row.get("id")]


def app_project_tree() -> list[dict[str, Any]]:
    """把扁平项目列表组装成树：[{id,name,children:[...]}]，保持服务端顺序。"""
    nodes: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for proj in app_projects():
        nodes[str(proj["id"])] = {
            "id": str(proj["id"]),
            "name": str(proj.get("name") or ""),
            "parent_id": proj.get("parent_id") or None,
            "children": [],
        }
    for node in nodes.values():
        parent = nodes.get(str(node["parent_id"] or ""))
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots
