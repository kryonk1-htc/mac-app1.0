"""配置与令牌存取。

服务器地址后台配好，用户不填（四级回落，顺序固定）：
    1. 命令行 --server <url>         临时调试/内网换机最方便
    2. %APPDATA%\\ORBYTE\\config.json  已经跑起来的环境（配对后自动写入）
    3. 编译期注入 _build_info.BUILD_SERVER_URL（-ServerUrl 打包）
    4. 内置 BUILTIN_SERVER            兜底：改这一行即可切环境

第 4 级保证**永远有地址**，所以首启不会再弹输入框——测试阶段指向本机
Nest（127.0.0.1:3000），部署后把 BUILTIN_SERVER 改成正式域名即可。

下载地址和版本号不在本地存：一律问服务端 `/api/orbyte/release`，
因为服务端每次现扫发布目录。本地留一份只会变成"部署后忘了改"的事故。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .db import database
from .paths import CONFIG_PATH, DEFAULT_SERVER

logger = logging.getLogger("orb.config")

#: 内置服务器地址（兜底，用户不可见、不需填写）
#: 测试阶段 = 本机 Nest；部署后改成 https://正式域名
BUILTIN_SERVER = "http://127.0.0.1:3000"

_META_TOKEN = "device_token"
_META_TOKEN_PENDING = "device_token_pending"
_META_DEVICE_NAME = "device_name"


# ---- 配置文件 ----


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("配置文件损坏，按空配置处理：%s", exc)
        return {}


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入（不覆盖别的键，避免升级后丢配置）。"""
    data = load_config()
    data.update(patch)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def resolve_server(cli_value: str | None = None) -> tuple[str, str]:
    """返回 (地址, 来源)。来源用于日志里说明"这个地址是哪来的"。

    永远返回非空地址（最后一级是 BUILTIN_SERVER），
    所以调用方不需要再处理"没有地址"的分支。
    """
    if cli_value:
        return cli_value.rstrip("/"), "cli"
    configured = (load_config().get("server") or "").strip()
    if configured:
        return configured.rstrip("/"), "config"
    if DEFAULT_SERVER:
        return DEFAULT_SERVER.rstrip("/"), "build"
    return BUILTIN_SERVER.rstrip("/"), "builtin"


def save_server(url: str) -> None:
    save_config({"server": url.rstrip("/")})


# ---- 设备令牌 ----


def get_token() -> str:
    return database.get_meta(_META_TOKEN)


def set_token(token: str) -> None:
    database.set_meta(_META_TOKEN, token)


def clear_token() -> None:
    database.clear_meta(_META_TOKEN)


# 配对中的令牌单独放：出码阶段还没被网页确认，这时如果写进 device_token，
# 界面会误以为"已连接"、同步线程也会开始发请求（然后被 401 打回来）。
def get_pending_token() -> str:
    return database.get_meta(_META_TOKEN_PENDING)


def set_pending_token(token: str) -> None:
    database.set_meta(_META_TOKEN_PENDING, token)


def clear_pending_token() -> None:
    """清掉「待确认」槽位。

    服务端判 401 时必须连 pending 一起清：配对流程会用
    `get_token() or get_pending_token()` 复用旧身份去重新出码，
    只清 device_token 的话，那枚已失效的令牌会被原样再交一次，
    服务端仍然认它是同一台被撤销的设备。
    """
    database.clear_meta(_META_TOKEN_PENDING)


def promote_pending() -> None:
    """网页点了「允许」之后才把待用令牌扶正。"""
    pending = database.get_meta(_META_TOKEN_PENDING)
    if pending:
        database.set_meta(_META_TOKEN, pending)
        database.clear_meta(_META_TOKEN_PENDING)


def get_device_name() -> str:
    stored = database.get_meta(_META_DEVICE_NAME)
    if stored:
        return stored
    import platform as _platform

    name = _platform.node() or "Windows 设备"
    database.set_meta(_META_DEVICE_NAME, name)
    return name


def platform_label() -> str:
    import platform as _platform

    return f"{_platform.system()} {_platform.release()}".strip()


# ---- 界面记忆（位置 / 开关）----
# 存 meta 而不是 config.json：这些是程序自己的状态，不是用户要读的配置。


def get_window_pos(key: str) -> tuple[int, int] | None:
    raw = database.get_meta(f"pos_{key}")
    if not raw:
        return None
    try:
        x_text, y_text = raw.split(",", 1)
        return int(x_text), int(y_text)
    except ValueError:
        return None


def save_window_pos(key: str, x: int, y: int) -> None:
    database.set_meta(f"pos_{key}", f"{int(x)},{int(y)}")


def get_theme() -> str:
    """界面主题：light / dark / system（默认跟随系统）。"""
    raw = (database.get_meta("ui_theme") or "").strip().lower()
    return raw if raw in ("light", "dark", "system") else "system"


def set_theme(mode: str) -> None:
    database.set_meta("ui_theme", mode)


def get_window_size(key: str) -> tuple[int, int] | None:
    """窗口尺寸记忆：用户拖过右下角就按他拖的来，别再自己算高度。"""
    raw = database.get_meta(f"size_{key}")
    if not raw:
        return None
    try:
        w_text, h_text = raw.split(",", 1)
        width, height = int(w_text), int(h_text)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def save_window_size(key: str, width: int, height: int) -> None:
    database.set_meta(f"size_{key}", f"{int(width)},{int(height)}")


def get_flag(key: str, default: bool = False) -> bool:
    raw = database.get_meta(f"flag_{key}")
    if not raw:
        return default
    return raw == "1"


def set_flag(key: str, value: bool) -> None:
    database.set_meta(f"flag_{key}", "1" if value else "0")


# ---- 发布目录（仅本地开发用：把打包产物复制进来即可，不改代码）----


def local_release_dir() -> Path | None:
    """源码运行时找仓库里的 release 目录，方便本地联调下载。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "release"
        if candidate.is_dir() and candidate.name == "release":
            return candidate
    return None
