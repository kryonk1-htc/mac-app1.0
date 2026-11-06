"""自检（无界面）：验证本地库、生活待办隔离、断点续传的关键行为。

跑法：
    python -m orbyte.selftest

设计原则：**不依赖网络**。断网场景才是重点，所以这里用一个假客户端
模拟"服务端只能确认前 3 条"，验证剩下的会退回 pending 重发。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 必须在 import orbyte.db 之前设好，否则会用真实数据目录
_TMP = Path(tempfile.mkdtemp(prefix="orbyte-selftest-"))
os.environ["ORBYTE_DB_PATH"] = str(_TMP / "test.db")

from . import todo_store  # noqa: E402
from .db import database  # noqa: E402
from .sync import SyncWorker  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name} {detail}")


def main() -> int:
    print(f"临时库：{os.environ['ORBYTE_DB_PATH']}")
    database.init_schema()

    print("\n[1] 断网写入 20 条工作待办")
    for i in range(20):
        todo_store.add(f"任务 {i}", "演示项目", space="work")
    check("本地落库 20 条", len(todo_store.list_todos("work")) == 20)
    check("待发队列 20 条", todo_store.pending_count() == 20,
          f"实际 {todo_store.pending_count()}")

    print("\n[2] 生活待办永不出本机（第一道拦截）")
    for i in range(5):
        todo_store.add(f"买菜 {i}", "生活", space="life")
    check("生活待办本地可见 5 条", len(todo_store.list_todos("life")) == 5)
    check("待发队列仍为 20 条（生活待办未排队）",
          todo_store.pending_count() == 20,
          f"实际 {todo_store.pending_count()}")

    print("\n[3] 三态切换与工时结算")
    target = todo_store.list_todos("work")[0]["uuid"]
    todo_store.set_state(target, "doing")
    check("active_session 有且仅有 1 条",
          database.conn().execute("SELECT COUNT(*) FROM active_session").fetchone()[0] == 1)
    todo_store.set_state(target, "done")
    check("离开 doing 后计时结束",
          database.conn().execute("SELECT COUNT(*) FROM active_session").fetchone()[0] == 0)
    check("状态已变 green(done)", todo_store.get_todo(target)["state"] == "done")

    print("\n[4] 崩溃恢复：inflight 退回 pending")
    batch = todo_store.fetch_pending(3)
    todo_store.mark_inflight([b["seq"] for b in batch])
    check("标记为 inflight",
          database.conn().execute(
              "SELECT COUNT(*) FROM outbox WHERE state='inflight'").fetchone()[0] == 3)
    restored = todo_store.reset_inflight()
    check("启动恢复退回 pending", restored == 3, f"实际 {restored}")

    print("\n[5] 部分确认：只 ack 服务端确认过的")
    before = todo_store.pending_count()
    batch = todo_store.fetch_pending(10)
    seqs = [b["seq"] for b in batch]
    todo_store.mark_inflight(seqs)
    todo_store.ack_upto(seqs[2])          # 服务端只认到第 3 条
    todo_store.reset_inflight()           # 其余退回 pending
    after = todo_store.pending_count()
    check("只少了已确认的 3 条", before - after == 3, f"{before} → {after}")
    check("已确认条目标记为 acked",
          database.conn().execute(
              "SELECT COUNT(*) FROM outbox WHERE state='acked'").fetchone()[0] >= 3)

    print("\n[6] 同一 uuid 连续编辑只推最新一条")
    uid = todo_store.list_todos("work")[1]["uuid"]
    todo_store.update(uid, title="改了一次")
    todo_store.update(uid, title="改了两次")
    todo_store.update(uid, title="改了三次")
    mine = [b for b in todo_store.fetch_pending(100) if b["uuid"] == uid]
    check("队列里该 uuid 只有 1 条", len(mine) == 1, f"实际 {len(mine)}")
    check("推的是最新内容", bool(mine) and mine[0]["title"] == "改了三次")

    print("\n[7] 离线不应把队列清空（假客户端不可用）")
    def no_client():
        return None
    worker = SyncWorker(no_client)
    check("无令牌时不同步、不丢数据",
          worker._push_once() is False and todo_store.pending_count() > 0)

    print(f"\n结果：{_passed} 通过 / {_failed} 失败")
    database.close()
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
