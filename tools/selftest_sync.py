"""同步自测：用本地假服务器跑通「推 → 拉 → 冲突」。

为什么要单独写：真实服务端要起 Nest、连 PG、还要走飞书登录，
每次改同步逻辑都靠手点验证，迟早漏掉"删除没有传"这类问题。
这里用一个 40 行的 http.server 顶替服务端，只验证客户端行为：

1. 推：工作待办全部带上新字段（优先级/截止日/计划工时）；
      生活待办一条都不能出现在请求里（三重保险的可见证据）
2. 确认：只有拿到 acked_upto 才算发完，outbox 要变 acked
3. 拉：远端新增/改标题/删除三种变化都能落进本地；
      拉下来的行**不能**再排进 outbox（否则会无限回声）
4. 冲突：本地有未推送的改动且更新，远端旧数据不能覆盖它

    python tools/selftest_sync.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["ORBYTE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="orbyte-sync-")) / "t.db")

from orbyte import config, todo_store  # noqa: E402
from orbyte.api_client import ApiClient  # noqa: E402
from orbyte.db import database  # noqa: E402
from orbyte.sync import SyncWorker  # noqa: E402

STATE: dict[str, dict] = {}
REQUESTS: list[dict] = []
REVISION = {"n": 0}
TOKEN_SEEN: list[str] = []
#: True 时服务端一律回 401，模拟"设备被撤销"
MODE = {"revoke": False}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # 静音
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        TOKEN_SEEN.append(self.headers.get("Authorization") or "")
        REQUESTS.append({"path": self.path, "body": body})
        if MODE["revoke"]:
            self._send({"error": {"message": "设备授权已失效，请在 ORBYTE 里重新配对"}}, 401)
            return
        if self.path != "/api/orbyte/sync/todos":
            self._send({"error": {"message": "not found"}}, 404)
            return
        batch = body.get("batch") or []
        acked = 0
        for item in batch:
            REVISION["n"] += 1
            STATE[item["uuid"]] = {**item, "revision": REVISION["n"]}
            acked = max(acked, int(item.get("seq") or 0))
        self._send({"acked_upto": acked, "accepted": len(batch)})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        TOKEN_SEEN.append(self.headers.get("Authorization") or "")
        if MODE["revoke"]:
            self._send({"error": {"message": "设备授权已失效，请在 ORBYTE 里重新配对"}}, 401)
            return
        if parsed.path != "/api/orbyte/sync/pull":
            self._send({"error": {"message": "not found"}}, 404)
            return
        cursor = int((parse_qs(parsed.query).get("cursor") or ["0"])[0] or 0)
        items = [item for item in STATE.values() if item["revision"] > cursor]
        items.sort(key=lambda item: item["revision"])
        next_cursor = items[-1]["revision"] if items else cursor
        self._send({"items": items, "next_cursor": next_cursor, "has_more": False})


def wait_until(predicate, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(("  OK   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    database.init_schema()
    config.set_token("dtk_" + "a" * 48)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    # ---- 造数据 ----
    a = todo_store.add("联调权限模块", "权限重构", priority="P1",
                       due_date="2026-11-02", planned_min=90)
    b = todo_store.add("写接口文档", "权限重构", priority="P3")
    c = todo_store.add("报销差旅", "日常事务")
    life = todo_store.add("买猫粮", "", space=todo_store.SPACE_LIFE, priority="P2")
    todo_store.set_state(b["uuid"], "doing")

    worker = SyncWorker(
        lambda: ApiClient(base, config.get_token(), timeout=5),
        on_status=lambda _p: None,
    )
    worker.start()

    print("1) 推送")
    ok = wait_until(lambda: todo_store.pending_count() == 0)
    check(ok, "待发队列清空（拿到 acked_upto 才算发完）")
    pushed = [req for req in REQUESTS if req["path"] == "/api/orbyte/sync/todos"]
    check(bool(pushed), "服务端收到了 /sync/todos")
    flat = [item for req in pushed for item in req["body"]["batch"]]
    check(len(flat) == 3, f"只推了 3 条工作待办（实际 {len(flat)}）")
    check(all(item["uuid"] != life["uuid"] for item in flat), "生活待办没有出现在请求里")
    payload = next((item for item in flat if item["uuid"] == a["uuid"]), {})
    check(payload.get("priority") == "P1", "优先级已上传")
    check(payload.get("due_date") == "2026-11-02", "截止日已上传")
    check(payload.get("planned_min") == 90, "计划工时已上传")
    check(all(header.startswith("Bearer dtk_") for header in TOKEN_SEEN),
          "请求带 Authorization: Bearer")

    # ---- 模拟远端变化 ----
    print("2) 拉取")
    worker.stop()
    worker.join(timeout=3)
    REVISION["n"] += 1
    STATE["remote-1"] = {
        "uuid": "remote-1",
        "title": "从网页新建的待办",
        "project": "日常事务",
        "state": "todo",
        "space": "work",
        "position": 99,
        "started_at": None,
        "spent_sec": 0,
        "priority": "P0",
        "due_date": "2026-10-30",
        "planned_min": 30,
        "updated_at": "2026-10-29T10:00:00+08:00",
        "deleted_at": None,
        "revision": REVISION["n"],
    }
    REVISION["n"] += 1
    STATE[c["uuid"]] = {**STATE[c["uuid"]],
                        "deleted_at": "2026-10-29T10:05:00+08:00",
                        "revision": REVISION["n"]}
    REVISION["n"] += 1
    STATE[a["uuid"]] = {**STATE[a["uuid"]],
                        "title": "联调权限模块（远端改名）",
                        "updated_at": "2026-10-29T10:06:00+08:00",
                        "revision": REVISION["n"]}

    worker = SyncWorker(
        lambda: ApiClient(base, config.get_token(), timeout=5),
        on_status=lambda _p: None,
    )
    worker.start()
    ok = wait_until(lambda: todo_store.get_todo("remote-1") is not None)
    check(ok, "远端新增的待办已落本地")
    remote = todo_store.get_todo("remote-1") or {}
    check(remote.get("priority") == "P0" and remote.get("due_date") == "2026-10-30",
          "拉下来的字段完整（优先级/截止日）")
    check(remote.get("sync_state") == "synced", "拉下来的行标记为已同步")
    deleted = todo_store.get_todo(c["uuid"]) or {}
    check(bool(deleted.get("deleted_at")), "远端的删除在本地生效（软删除）")
    check((todo_store.get_todo(a["uuid"]) or {}).get("title") == "联调权限模块（远端改名）",
          "远端改名覆盖了本地")
    check(todo_store.pending_count() == 0, "拉取没有产生回声（outbox 仍为空）")

    # ---- 冲突：本地改动更新 ----
    print("3) 冲突")
    todo_store.update(b["uuid"], title="写接口文档（本地刚改）")
    REVISION["n"] += 1
    STATE[b["uuid"]] = {**STATE[b["uuid"]],
                        "title": "写接口文档（远端旧值）",
                        "updated_at": "2026-10-01T09:00:00+08:00",
                        "revision": REVISION["n"]}
    worker.trigger()
    time.sleep(2.0)
    check((todo_store.get_todo(b["uuid"]) or {}).get("title") == "写接口文档（本地刚改）",
          "本地较新的改动没有被远端旧值覆盖")
    check((STATE.get(b["uuid"]) or {}).get("title") == "写接口文档（本地刚改）",
          "下一轮推送把本地改动覆盖回服务端（冲突以本地为准）")
    check(todo_store.pending_count() == 0, "推完之后队列回到空")

    worker.stop()
    worker.join(timeout=3)

    # ---- 401：令牌被服务端判死 ----
    print("4) 401 令牌失效")
    # 模拟"本地还留着令牌、服务端已经撤销"的场景
    config.set_token("dtk_" + "c" * 48)
    config.set_pending_token("dtk_" + "d" * 48)
    todo_store.update(a["uuid"], title="令牌失效期间改的标题")
    pending_before = todo_store.pending_count()
    check(pending_before > 0, "造出一条待发改动（前置条件）")

    MODE["revoke"] = True
    events: list[dict] = []
    worker = SyncWorker(
        lambda: ApiClient(base, config.get_token(), timeout=5),
        on_status=events.append,
    )
    worker.start()
    ok = wait_until(lambda: any(e.get("state") == "unauthorized" for e in events))
    check(ok, "收到 401 后广播 unauthorized")
    check(config.get_token() == "", "401 后 device_token 已清空")
    check(config.get_pending_token() == "", "401 后 device_token_pending 已清空")
    check(todo_store.pending_count() == pending_before,
          f"本地改动一条没丢（仍待发 {todo_store.pending_count()} 条）")
    relogins = [e for e in events if e.get("relogin")]
    check(len(relogins) == 1, f"relogin 只置真一次（实际 {len(relogins)} 次）")

    worker.stop()
    worker.join(timeout=3)
    MODE["revoke"] = False
    server.shutdown()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "；".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
