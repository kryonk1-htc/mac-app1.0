"""设备码配对（短期码 → 长期令牌）。

流程与「扫码登录」同理，但更适合企业内网：

    插件出码 482913  →  浏览器打开 /desktop/pair?code=482913
    （浏览器里本来就是登录态）→ 点「允许」→ 插件拿到授权

关键设计：**令牌由插件自己生成，明文从不经过网络，也从不落到服务端**。
配对时只把 sha256 交上去，之后每次请求带明文、服务端算 hash 比对。
所以服务端数据库被拖走，也拿不到任何一台设备的可用令牌。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any, Callable

from . import config
from .api_client import ApiClient, ApiError, NetworkError

logger = logging.getLogger("orb.pair")

#: 配对轮询间隔（秒）。服务端码有效期 10 分钟，2 秒一次足够灵敏也不吵
POLL_INTERVAL_SEC = 2


def new_device_token() -> str:
    """生成设备令牌（只在本机存在明文）。"""
    return f"dtk_{secrets.token_hex(24)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingSession:
    """一次配对的生命周期：出码 → 等人点允许 → 落令牌。"""

    def __init__(self, client: ApiClient, app_version: str) -> None:
        self._client = client
        self._app_version = app_version
        self.code: str = ""
        self.confirm_url: str = ""
        self.expires_at: str = ""
        self._token: str = ""

    def start(self) -> None:
        """申请配对码。重复调用会复用同一枚令牌（设备身份不变）。"""
        existing = config.get_token() or config.get_pending_token()
        self._token = existing or new_device_token()
        # 先落本地：万一用户点完「允许」时插件被关掉，重启后还能认领。
        # 注意存的是"待用"槽位——没被网页确认前不算已连接。
        if not existing:
            config.set_pending_token(self._token)

        result = self._client.pair_start(
            device_name=config.get_device_name(),
            platform=config.platform_label(),
            token_hash=token_hash(self._token),
            app_version=self._app_version,
        )
        self.code = str(result.get("code") or "")
        self.confirm_url = str(result.get("confirm_url") or "")
        self.expires_at = str(result.get("expires_at") or "")
        logger.info("配对码已生成：%s（%s 失效）", self.code, self.expires_at)

    def poll(self) -> str:
        """查一次状态：pending / active / expired / revoked。

        active 之后刻意**不清除本地令牌**——那就是正常状态。
        """
        if not self._token:
            self._token = config.get_token() or config.get_pending_token()
        if not self._token:
            return "expired"
        result = self._client.pair_poll(
            code=None, token_hash=token_hash(self._token)
        )
        status = str(result.get("status") or "pending")
        if status == "active":
            # 网页已点允许：这才把令牌扶正，同步线程开始工作
            config.promote_pending()
            logger.info("设备已授权：%s", result.get("device_name"))
        elif status == "revoked":
            logger.warning("设备授权已被撤销，需要重新配对")
        return status

    @property
    def token(self) -> str:
        return self._token or config.get_token() or config.get_pending_token()


def wait_for_pairing(
    client: ApiClient,
    app_version: str,
    on_code: Callable[[PairingSession], None] | None = None,
    timeout_sec: int = 600,
) -> bool:
    """阻塞式配对（供 selftest / 无界面场景用）。界面走 QTimer 自己轮询。"""
    session = PairingSession(client, app_version)
    session.start()
    if on_code:
        on_code(session)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if session.poll() == "active":
                return True
        except NetworkError:
            pass
        except ApiError as exc:
            logger.warning("配对轮询失败：%s", exc.message)
        time.sleep(POLL_INTERVAL_SEC)
    return False
