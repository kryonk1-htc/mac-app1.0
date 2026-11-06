"""路径与运行时常量（唯一配置源）。

所有"东西放哪"的判断都集中在这里，其它模块一律 `from .paths import X`，
禁止再各自 os.path.join 拼路径——散落的路径是换部署环境时最容易漏的地方。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import _build_info, __version__

#: 应用名（目录名、注册表项、协议名统一用它）
APP_NAME = "ORBYTE"
#: 自定义协议，网页上点「打开插件」靠它
PROTOCOL = "orbyte"

# ---- 运行时模式 ----

#: PyInstaller 单文件模式：资源被解到临时目录，代码在 sys._MEIPASS
FROZEN = getattr(sys, "frozen", False)
#: 打包后的资源根（html/图标）；源码运行时是包目录
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
#: 资源真正所在的目录。
#: 源码运行：src/orbyte/{web,assets}
#: 打包运行：--add-data 的落点是 _MEIPASS/orbyte/{web,assets}
#: （漏掉这个 orbyte/ 前缀就会打包后找不到图标，只能退回兜底绘制）
PKG_DIR = (RES_DIR / "orbyte") if FROZEN else RES_DIR

# ---- 数据目录 ----

if sys.platform == "darwin":
    # macOS 惯例：库与配置都进 Application Support（Windows 那套
    # AppData 环境变量在 mac 上不存在，硬算会落出一个 ~/AppData 怪目录）
    _support = Path.home() / "Library" / "Application Support"
    #: 本地库与日志（跟随机器，不漫游：SQLite 放漫游目录会跨机器同步坏掉）
    DATA_DIR = _support / APP_NAME
    #: 配置（服务器地址等，量小，放漫游无妨）
    CONFIG_DIR = _support / APP_NAME
else:
    _local = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    _roaming = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    #: 本地库与日志（跟随机器，不漫游）
    DATA_DIR = Path(_local) / APP_NAME
    #: 配置（服务器地址等，量小，放漫游无妨）
    CONFIG_DIR = Path(_roaming) / APP_NAME

DB_PATH = Path(os.getenv("ORBYTE_DB_PATH") or (DATA_DIR / "orbyte.db"))
LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "orbyte.log"
CONFIG_PATH = CONFIG_DIR / "config.json"
#: 生活待办导出（卸载前提示用，永不外传）
EXPORT_DIR = DATA_DIR / "export"
#: 暂存区图片（仅本机；库表里只存文件名，本体在这里）
SCRATCH_IMG_DIR = DATA_DIR / "scratch-images"

WEB_DIR = PKG_DIR / "web"
ASSET_DIR = PKG_DIR / "assets"

VERSION = _build_info.BUILD_VERSION or __version__

#: 默认服务器地址（四级回落的第一级由命令行覆盖，这是第三级）
DEFAULT_SERVER = _build_info.BUILD_SERVER_URL

#: 托盘/窗口图标。
#: macOS 上 Qt 未必带 qico 图像插件，优先 png；Windows 仍只用原来的 ico，
#: 找不到 png 时回落到 ico（跟以前一份文件走天下的行为兼容）。
_ICON_CANDIDATES = (
    (ASSET_DIR / "orbyte.png", ASSET_DIR / "orbyte.icns")
    if sys.platform == "darwin"
    else (ASSET_DIR / "orbyte.ico",)
)
ICON_FILE = next(
    (p for p in _ICON_CANDIDATES if p.exists()), ASSET_DIR / "orbyte.ico"
)

# ---- 同步参数 ----

#: 单批最多推多少条（与服务端 MAX_BATCH 对齐）
SYNC_BATCH = 100
#: 空闲时的轮询间隔（秒）；有积压时按退避缩短到 BASE
SYNC_IDLE_SEC = 30
SYNC_BASE_SEC = 3
#: 指数退避上限（秒）
SYNC_MAX_BACKOFF_SEC = 300

#: 本地 HTTP 桥只绑回环
BRIDGE_HOST = "127.0.0.1"


def ensure_dirs() -> None:
    """创建所有必须存在的目录（幂等）。"""
    for path in (DATA_DIR, LOG_DIR, CONFIG_DIR, EXPORT_DIR, SCRATCH_IMG_DIR):
        path.mkdir(parents=True, exist_ok=True)
