"""日志配置（一处配置，全局生效）。

单文件模式下没有控制台，日志是唯一排障手段，所以默认就把
文件 handler 挂上，且带滚动——插件常驻几天，日志不清会吃满磁盘。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import LOG_PATH, ensure_dirs

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """配置 root logger：控制台 + 文件双 handler（重复调用无副作用）。"""
    global _configured
    if _configured:
        return
    ensure_dirs()

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    # 打包成 windowed exe 后 sys.stderr 是 None，加了会抛异常
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)

    # 第三方库太吵，压到 WARNING
    for noisy in ("urllib3", "PyQt5"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
