"""HTTP 客户端（只用标准库）。

两个必须做对的地方，都是踩过坑写下来的：

1. **绕开系统代理**。本机 http_proxy 指向 127.0.0.1:59701，urllib 默认会
   把 127.0.0.1 的请求也丢给代理，结果 502。所以 opener 固定用
   `ProxyHandler({})`，不走环境变量。
2. **Bearer 令牌**。设备令牌 `dtk_...` 和网页会话是同一套中间件，
   所以这里只要一个头，服务端就能换出 userId。
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger("orb.api")

#: 网络类错误（断网、超时、DNS 失败）：调用方应退避重试而不是当成业务失败
NETWORK_ERRORS = (urllib.error.URLError, socket.timeout, TimeoutError, OSError)


class ApiError(Exception):
    """服务端返回了非 2xx。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class AuthError(ApiError):
    """401：令牌失效/设备被撤销，需要重新配对。"""


class NetworkError(Exception):
    """连不上服务器（离线、断网、地址错）。"""


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # 关键：空 ProxyHandler = 全程不走代理
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPRedirectHandler(),
        )

    # ---- 基础 ----

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        body = None
        headers = {"Accept": "application/json", "User-Agent": "ORBYTE-Desktop"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._to_api_error(exc) from exc
        except NETWORK_ERRORS as exc:
            raise NetworkError(str(exc)) from exc

        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(500, f"响应不是 JSON: {raw[:120]!r}") from exc

    def _to_api_error(self, exc: urllib.error.HTTPError) -> ApiError:
        message = f"HTTP {exc.code}"
        try:
            raw = exc.read().decode("utf-8", "replace")
            data = json.loads(raw)
            # 本项目错误体有两种：{code,message} 与 {error:{code,message}}
            if isinstance(data, dict):
                inner = data.get("error")
                if isinstance(inner, dict) and inner.get("message"):
                    message = str(inner["message"])
                elif data.get("message"):
                    message = str(data["message"])
        except Exception:  # noqa: BLE001 - 解析失败不该掩盖原始状态码
            pass
        if exc.code == 401:
            return AuthError(401, message)
        return ApiError(exc.code, message)

    # ---- 业务接口（路径与服务端 orbyte.controller.ts 一一对应）----

    def pair_start(
        self, device_name: str, platform: str, token_hash: str, app_version: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/orbyte/pair/start",
            {
                "device_name": device_name,
                "platform": platform,
                "token_hash": token_hash,
                "app_version": app_version,
            },
        )

    def pair_poll(self, code: str | None, token_hash: str) -> dict[str, Any]:
        query = {"token_hash": token_hash} if not code else {"code": code}
        return self._request("GET", "/api/orbyte/pair/poll", query=query)

    def sync_todos(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/orbyte/sync/todos", {"batch": batch})

    def pull_todos(self, cursor: int = 0) -> dict[str, Any]:
        return self._request(
            "GET", "/api/orbyte/sync/pull", query={"cursor": str(max(0, int(cursor)))}
        )

    def pull_app_logs(self) -> dict[str, Any]:
        """应用 → 插件：个人项目树 + 日清条目全量快照（双向同步的下行）。"""
        return self._request("GET", "/api/orbyte/sync/app-logs")

    def release(self) -> dict[str, Any] | None:
        return self._request("GET", "/api/orbyte/release")
