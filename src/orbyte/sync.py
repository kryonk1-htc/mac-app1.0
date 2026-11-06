"""断点续传（outbox 推送 + 增量拉取）。

"断点续传"不是"失败重试"，靠的是四件事一起成立：

1. 落盘排队：改动与 outbox 同事务写入（见 todo_store），断电也不丢。
2. 状态机 pending → inflight → acked：只有拿到服务端的 acked_upto
   才算成功，其余退回 pending 重发。宁可重发，不可漏发。
3. 服务端幂等：按 (user_id, uuid) upsert，同一条推十次结果一样。
4. 崩溃恢复：进程启动时把 inflight 全部退回 pending
   （数据只在内存里"已发出"，服务端没确认就等于没发）。

推送之后还会拉一次：换机器、重装、第二台设备都靠它把数据取回来。
游标是服务端的 revision 整数，不是时间戳——两台机器的钟不可信。

离线时这个线程什么都不做，只是按退避重试；本地读写照常，界面零等待。
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Any, Callable

from . import config, todo_store
from .api_client import ApiClient, ApiError, AuthError, NetworkError
from .db import database
from .paths import (
    SYNC_BASE_SEC,
    SYNC_BATCH,
    SYNC_IDLE_SEC,
    SYNC_MAX_BACKOFF_SEC,
)

logger = logging.getLogger("orb.sync")

_CURSOR_KEY = "pull_cursor"

#: on_status 回调收到的状态字典
#: {state: 'idle'|'syncing'|'offline'|'unauthorized'|'error', pending: int, note: str,
#:  pulled: int, relogin: bool}
#: relogin=True 只在「刚刚判定令牌失效」的那一次为真，主线程据此弹配对窗；
#: 后续轮次一律 False，否则窗口会被反复弹出来。
StatusCallback = Callable[[dict[str, Any]], None]


class SyncWorker(threading.Thread):
    """后台推送线程。

    生命周期：创建即 start → 循环 → stop() 后自然退出。
    没有令牌（未配对）时线程也照跑，只是不做网络请求，
    这样配对完成后不需要重启线程就能立即开始同步。
    """

    def __init__(
        self,
        client_factory: Callable[[], ApiClient | None],
        on_status: StatusCallback | None = None,
    ) -> None:
        super().__init__(name="orbyte-sync", daemon=True)
        self._client_factory = client_factory
        self._on_status = on_status
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state = "idle"
        self._note = ""
        #: 累计拉取并进本地的条数：界面靠它的变化决定要不要重画列表
        self._pulled = 0
        #: 本轮失效是否已经报过（401 后清令牌，只让主线程弹一次配对窗）
        self._auth_reported = False

    # ---- 外部接口 ----

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def trigger(self) -> None:
        """本地刚改完，立刻推一次（不用等空闲轮询）。"""
        self._wake_event.set()

    @property
    def state(self) -> str:
        return self._state

    # ---- 状态广播 ----

    def _emit(self, state: str, note: str = "", relogin: bool = False) -> None:
        self._state = state
        self._note = note
        if self._on_status:
            try:
                self._on_status(
                    {
                        "state": state,
                        "pending": todo_store.pending_count(),
                        "note": note,
                        "pulled": self._pulled,
                        "relogin": relogin,
                    }
                )
            except Exception:  # noqa: BLE001 - UI 回调异常不能带崩同步线程
                logger.exception("状态回调异常")

    # ---- 主循环 ----

    def run(self) -> None:
        reset = todo_store.reset_inflight()
        if reset:
            logger.info("启动恢复：%d 条 inflight 退回 pending", reset)

        backoff = SYNC_BASE_SEC
        while not self._stop_event.is_set():
            try:
                did_push = self._push_once()
                # 推完再拉：两件事共用一个退避，离线时不会各打一次网络
                did_pull = self._pull_once()
                # 应用侧全量快照（项目树 + 日清条目）：双向同步的下行半边。
                # 快照很小（人手量级），每轮都拉一次最省心，不做增量游标
                did_app = self._pull_app_once()
            except AuthError as exc:
                # 令牌被服务端判死（设备撤销 / 记录丢失）：必须就地清空本地令牌。
                # 不清的话界面会永远显示"已连接"，而"连接账号"又因为令牌非空
                # 直接短路成"已经连接"的提示——用户从此没有任何回到配对的路。
                logger.warning("设备授权失效：%s", exc.message)
                config.clear_token()
                config.clear_pending_token()
                self._emit(
                    "unauthorized",
                    "连接已失效，需要重新配对",
                    relogin=not self._auth_reported,
                )
                self._auth_reported = True
                # 清完之后 client_factory() 会返回 None，下一轮直接走 idle，
                # 不会每 30 秒再打一次必然失败的请求。
                self._wait(SYNC_IDLE_SEC)
                continue
            except NetworkError as exc:
                logger.info("离线，%.0fs 后重试（%s）", backoff, exc)
                self._emit("offline", "当前无网络，改动已存在本机")
                self._wait(backoff)
                backoff = min(SYNC_MAX_BACKOFF_SEC, max(SYNC_BASE_SEC, backoff * 2))
                continue
            except ApiError as exc:
                logger.warning("同步被拒：%s %s", exc.status, exc.message)
                self._emit("error", exc.message)
                self._wait(backoff)
                backoff = min(SYNC_MAX_BACKOFF_SEC, max(SYNC_BASE_SEC, backoff * 2))
                continue
            except Exception as exc:  # noqa: BLE001 - 兜底，线程不能死
                logger.exception("同步线程异常")
                self._emit("error", str(exc))
                self._wait(backoff)
                continue

            # 成功一轮：退避归零，失效标记也复位（下次真失效还要能弹窗）
            backoff = SYNC_BASE_SEC
            self._auth_reported = False
            if did_push:
                self._emit("idle")
                # 有活干就立刻看下一批，别等空闲间隔
                continue
            self._emit("idle")
            self._wait(SYNC_IDLE_SEC + random.uniform(0, 3))

    def _wait(self, seconds: float) -> None:
        """可被 stop()/trigger() 提前唤醒的等待。"""
        self._wake_event.wait(timeout=seconds)
        self._wake_event.clear()

    def _push_once(self) -> bool:
        """推一批。返回 True 表示确实推了东西。"""
        client = self._client_factory()
        if client is None:
            # 还没配对：不报错，安静等着
            self._emit("idle", "尚未配对")
            return False

        items = todo_store.fetch_pending(SYNC_BATCH)
        if not items:
            return False

        # 第二道拦截：发送层再过滤一次 work（防上游代码写错）
        batch: list[dict[str, Any]] = []
        skipped = 0
        for item in items:
            if item.get("space") != todo_store.SPACE_WORK:
                skipped += 1
                continue
            batch.append(
                {
                    "seq": int(item["seq"]),
                    "uuid": item["uuid"],
                    "title": item["title"],
                    "project": item["project"],
                    "state": item["state"],
                    "space": "work",
                    "position": int(item.get("position") or 0),
                    "started_at": item.get("started_at"),
                    "spent_sec": int(item.get("spent_sec") or 0),
                    "priority": item.get("priority") or "",
                    "due_date": item.get("due_date"),
                    "planned_min": int(item.get("planned_min") or 0),
                    "updated_at": item["updated_at"],
                    "deleted_at": item.get("deleted_at"),
                    # 双向同步映射键：让服务端知道这条对应应用里哪一行日清
                    "log_id": item.get("log_id"),
                }
            )
        if skipped:
            logger.warning("发送层拦下 %d 条非工作待办（不应发生）", skipped)
        if not batch:
            return False

        todo_store.mark_inflight([entry["uuid"] for entry in batch])
        self._emit("syncing", f"正在上传 {len(batch)} 条")
        try:
            result = client.sync_todos(batch)
        except Exception:
            # 关键：失败必须把 inflight 退回 pending，否则这些条目会卡住
            todo_store.reset_inflight()
            raise

        acked = int((result or {}).get("acked_upto") or 0)
        if acked <= 0:
            todo_store.reset_inflight()
            raise ApiError(500, "服务端未返回 acked_upto，按未确认处理")
        confirmed = todo_store.ack_upto(acked)
        logger.info("同步成功：本批 %d 条，确认 %d 条", len(batch), confirmed)
        return True

    def _pull_once(self) -> bool:
        """拉一批服务端比自己新的记录。返回 True 表示确实并进了数据。

        游标存在 meta 表（落盘），重启不会从头拉；拉下来的行不进 outbox，
        否则会变成"拉下来又推回去"的死循环。
        """
        client = self._client_factory()
        if client is None:
            return False

        cursor = int(database.get_meta(_CURSOR_KEY, "0") or 0)
        result = client.pull_todos(cursor) or {}
        items = result.get("items") or []
        next_cursor = int(result.get("next_cursor") or cursor)
        has_more = bool(result.get("has_more"))

        if not items:
            if next_cursor != cursor:
                database.set_meta(_CURSOR_KEY, str(next_cursor))
            return False

        stats = todo_store.apply_remote(items)
        database.set_meta(_CURSOR_KEY, str(next_cursor))
        self._pulled += int(stats["added"]) + int(stats["updated"])
        logger.info(
            "拉取：新增 %d 条，更新 %d 条，跳过 %d 条",
            stats["added"], stats["updated"], stats["skipped"],
        )
        # 还有下一页就立刻再来一次，别等下一轮空闲
        if has_more:
            self._wake_event.set()
        return bool(stats["added"] or stats["updated"])

    def _pull_app_once(self) -> bool:
        """拉一次应用侧快照（个人项目树 + 日清条目）并镜像进本地库。

        返回 True 表示本地数据有变化（新增/更新/移除），界面需要重画。
        失败不抛出：快照是全量，下一轮自然重试；抛了反而打断 push/pull 的退避。
        """
        client = self._client_factory()
        if client is None:
            return False
        try:
            snapshot = client.pull_app_logs() or {}
        except AuthError:
            raise  # 令牌失效必须冒泡，主循环要清令牌弹配对窗
        except Exception as exc:  # noqa: BLE001 - 快照失败不阻塞主循环
            logger.warning("应用快照拉取失败（下轮重试）：%s", exc)
            return False

        stats = todo_store.apply_app_snapshot(
            snapshot.get("projects") or [], snapshot.get("items") or []
        )
        changed = bool(stats["added"] or stats["updated"] or stats["removed"])
        if changed:
            self._pulled += int(stats["added"]) + int(stats["updated"])
            logger.info(
                "应用快照：新增 %d、更新 %d、移除 %d、跳过 %d",
                stats["added"], stats["updated"], stats["removed"], stats["skipped"],
            )
        return changed
