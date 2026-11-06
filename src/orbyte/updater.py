"""版本检查（不自动替换 exe）。

为什么不做静默替换：插件运行时自己的 exe 被占用，Windows 上必须靠
一个批处理脚本"等进程退出后覆盖再重启"，中途任何一步失败都会留下
一个打不开的程序。对内部工具来说，把新版本下载页打开、让人自己双击，
比赌一次文件替换可靠得多。

检查时机：启动后 5 秒（不抢启动速度）+ 托盘手动点「检查更新」。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PyQt5.QtCore import QObject, pyqtSignal

from .api_client import ApiClient

logger = logging.getLogger("orb.update")


def parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


class UpdateChecker(QObject):
    """后台线程里问 /release，主线程里用信号回结果。"""

    found = pyqtSignal(dict)
    upToDate = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(
        self,
        client_factory: Callable[[], ApiClient | None],
        current_version: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client_factory = client_factory
        self._current = current_version
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def check_async(self) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._run, name="orbyte-update", daemon=True).start()

    def _run(self) -> None:
        try:
            client = self._client_factory()
            if client is None:
                self.failed.emit("未连接账号，暂时不能检查更新")
                return
            info: dict[str, Any] | None = client.release()
            if not info:
                self.failed.emit("服务端还没有可下载的安装包")
                return
            version = str(info.get("version") or "")
            if is_newer(version, self._current):
                self.found.emit(info)
            else:
                self.upToDate.emit()
        except Exception as exc:  # noqa: BLE001 - 检查更新失败不该弹异常栈
            logger.info("检查更新失败：%s", exc)
            self.failed.emit("连不上服务器，稍后再试")
        finally:
            self._busy = False
