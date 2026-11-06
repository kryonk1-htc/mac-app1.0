"""本地 HTTP 桥（面板 ↔ Python 的唯一通道）。

为什么用本地 HTTP 而不是 WebChannel / COM 事件：
- conda 版 PyQt5 根本没有 qtwebchannel.js，QWebChannel 这条路是断的
- WebView2 的 WebMessage 事件需要手搓多个 COM 接口，风险高
- 本地 HTTP 只用标准库，同源同端口，面板里的 fetch 直接可用

安全：只绑 127.0.0.1（回环，外网进不来），且每次启动生成随机 token，
面板 HTML 由服务端注入这个 token，其它进程猜不到、调不动。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import __version__, config, todo_store
from .paths import WEB_DIR

logger = logging.getLogger("orb.bridge")

#: 面板里注入 token 的占位符（HTML 里写成 window.__ORBYTE_TOKEN__ = "__TOKEN__"）
TOKEN_PLACEHOLDER = "__ORBYTE_TOKEN__"


class BridgeServer:
    """极简 HTTP 服务：一份 HTML + 若干 JSON 接口。"""

    def __init__(
        self,
        on_pair_start: Callable[[], dict[str, Any]],
        on_sync_now: Callable[[], None],
        status_provider: Callable[[], dict[str, Any]],
        on_quit: Callable[[], None],
    ) -> None:
        self.token = secrets.token_urlsafe(24)
        self._on_pair_start = on_pair_start
        self._on_sync_now = on_sync_now
        self._status = status_provider
        self._on_quit = on_quit
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/panel.html?t={self.token}"

    def start(self) -> str:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            # 默认实现会往 stderr 打一堆日志，单文件模式下 stderr 可能是 None
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                logger.debug("bridge: " + fmt, *args)

            def do_GET(self) -> None:  # noqa: N802
                bridge._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                bridge._handle(self, "POST")

            def do_DELETE(self) -> None:  # noqa: N802
                bridge._handle(self, "DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="orbyte-bridge", daemon=True
        )
        self._thread.start()
        logger.info("本地桥已启动：127.0.0.1:%d", self.port)
        return self.url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # ---- 路由 ----

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 面板 HTML 本身允许用 ?t= 传 token（浏览器导航没法带自定义头）
        if path in ("/panel.html", "/"):
            provided = (query.get("t") or [""])[0]
            if provided != self.token:
                self._send(handler, 403, {"error": "invalid token"})
                return
            self._serve_panel(handler)
            return

        # 其余接口一律要求自定义头
        if handler.headers.get("X-Orbyte-Token") != self.token:
            self._send(handler, 403, {"error": "invalid token"})
            return

        try:
            self._route(handler, method, path)
        except ValueError as exc:
            self._send(handler, 400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 桥不能因为单个请求挂掉
            logger.exception("桥接口异常 %s %s", method, path)
            self._send(handler, 500, {"error": str(exc)})

    def _route(self, handler: BaseHTTPRequestHandler, method: str, path: str) -> None:
        body = self._read_json(handler) if method in ("POST", "DELETE") else {}

        if path == "/api/state" and method == "GET":
            self._send(handler, 200, self._state_payload())
            return

        if path == "/api/todos" and method == "POST":
            space = str(body.get("space") or todo_store.SPACE_WORK)
            if space not in (todo_store.SPACE_WORK, todo_store.SPACE_LIFE):
                raise ValueError("space 只能是 work 或 life")
            title = str(body.get("title") or "").strip()
            if not title:
                raise ValueError("标题不能为空")
            item = todo_store.add(title, str(body.get("project") or ""), space=space)
            self._on_sync_now()
            self._send(handler, 200, {"item": item, **self._state_payload()})
            return

        if path.startswith("/api/todos/"):
            rest = path[len("/api/todos/"):]
            parts = [p for p in rest.split("/") if p]
            if not parts:
                raise ValueError("缺少 uuid")
            item_uuid = parts[0]

            if len(parts) == 1 and method == "DELETE":
                ok = todo_store.delete(item_uuid)
                self._on_sync_now()
                self._send(handler, 200, {"ok": ok, **self._state_payload()})
                return

            if len(parts) == 1 and method == "POST":
                todo_store.update(
                    item_uuid,
                    title=body.get("title"),
                    project=body.get("project"),
                )
                self._on_sync_now()
                self._send(handler, 200, self._state_payload())
                return

            if len(parts) == 2 and parts[1] == "state" and method == "POST":
                todo_store.set_state(item_uuid, str(body.get("state") or "todo"))
                self._on_sync_now()
                self._send(handler, 200, self._state_payload())
                return

        if path == "/api/pair/start" and method == "POST":
            self._send(handler, 200, self._on_pair_start())
            return

        if path == "/api/sync/now" and method == "POST":
            self._on_sync_now()
            self._send(handler, 200, self._status())
            return

        if path == "/api/settings" and method == "POST":
            server = str(body.get("server") or "").strip()
            if server:
                config.save_server(server)
            self._send(handler, 200, {"server": config.resolve_server()[0]})
            return

        if path == "/api/release" and method == "GET":
            self._send(handler, 200, self._release_payload())
            return

        if path == "/api/quit" and method == "POST":
            self._send(handler, 200, {"ok": True})
            self._on_quit()
            return

        self._send(handler, 404, {"error": "not found"})

    # ---- 数据组装 ----

    def _state_payload(self) -> dict[str, Any]:
        work = todo_store.list_todos(todo_store.SPACE_WORK)
        life = todo_store.list_todos(todo_store.SPACE_LIFE)
        return {
            "work": work,
            "life": life,
            "counts": todo_store.counts(todo_store.SPACE_WORK),
            "active": todo_store.active_uuid(),
            "sync": self._status(),
            "server": config.resolve_server()[0],
            "device": config.get_device_name(),
            "paired": bool(config.get_token()),
            "version": __version__,
        }

    def _release_payload(self) -> dict[str, Any]:
        """发布包信息一律问服务端（服务端现扫目录），本地不留副本。"""
        from .api_client import ApiClient, ApiError, NetworkError

        server, _ = config.resolve_server()
        if not server:
            return {"error": "尚未配置服务器地址"}
        client = ApiClient(server, config.get_token())
        try:
            return client.release() or {"error": "服务端还没有发布包"}
        except NetworkError as exc:
            return {"error": f"连不上服务器：{exc}"}
        except ApiError as exc:
            return {"error": exc.message}

    # ---- 工具 ----

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是合法 JSON") from exc

    def _send(self, handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    def _serve_panel(self, handler: BaseHTTPRequestHandler) -> None:
        html_path = Path(WEB_DIR) / "panel.html"
        if not html_path.exists():
            self._send(handler, 404, {"error": f"缺少面板文件 {html_path}"})
            return
        html = html_path.read_text(encoding="utf-8").replace(
            TOKEN_PLACEHOLDER, self.token
        )
        data = html.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    def serve_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:  # pragma: no cover
        """预留给后续加图标/字体用（当前面板是单文件，不需要）。"""
        target = Path(WEB_DIR) / path.lstrip("/")
        if not target.exists():
            self._send(handler, 404, {"error": "not found"})
            return
        data = target.read_bytes()
        handler.send_response(200)
        handler.send_header(
            "Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        )
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
