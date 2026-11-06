"""Windows 集成：开机自启、orbyte:// 协议、导出生活待办、打开路径。

全部只写 HKCU（不需要管理员权限，也不碰系统级设置）。任何一步失败
都只记日志、不弹错——这些是"锦上添花"，不该让主程序起不来。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, todo_store

logger = logging.getLogger("orb.system")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "ORBYTE"
_PROTOCOL_KEY = r"Software\Classes\orbyte"


def app_command() -> str:
    """开机自启要执行的命令行。

    打包后就是 exe 自己；源码运行时是 python + app.py，
    两者的区别在这里兜住，别的地方不用判断 frozen。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = Path(sys.argv[0]).resolve()
    return f'"{sys.executable}" "{script}"'


# ---- 开机自启 ----


def autostart_enabled() -> bool:
    if sys.platform == "darwin":
        # macOS 版暂不实现开机自启（要用 LaunchAgents，且打包后路径
        # 会随 .app 移动而失效）。返回 False 让菜单与球的状态一致。
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, _VALUE_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("读开机自启失败：%s", exc)
        return False


def set_autostart(enabled: bool) -> bool:
    """写入/删除 HKCU Run 项。返回是否成功（失败不影响主流程）。"""
    if sys.platform == "darwin":
        return False  # 见 autostart_enabled 的说明
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, app_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        logger.info("开机自启已%s", "开启" if enabled else "关闭")
        return True
    except OSError as exc:
        logger.warning("设置开机自启失败：%s", exc)
        return False


# ---- orbyte:// 协议 ----


def register_protocol() -> None:
    """注册 orbyte:// 协议，网页上「打开 ORBYTE」能直接唤起。

    已注册就不再写，避免每次启动都动注册表。
    macOS 不需要这一步：协议在 Info.plist 的 CFBundleURLTypes 里声明，
    系统按 bundle 注册，运行时不碰任何全局状态。
    """
    if sys.platform == "darwin":
        return
    try:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROTOCOL_KEY):
                return
        except FileNotFoundError:
            pass

        command = f'{app_command()} "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTOCOL_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:ORBYTE Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, _PROTOCOL_KEY + r"\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        logger.info("已注册 orbyte:// 协议")
    except OSError as exc:
        logger.warning("注册协议失败：%s", exc)


# ---- 导出生活待办 ----


def default_export_path() -> Path:
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    base = desktop if desktop.is_dir() else Path.home()
    return base / f"ORBYTE-生活待办-{todo_store.today_str()}.json"


def export_life(path: Path) -> int:
    """把生活待办写成 JSON（不含任何服务器地址/令牌）。返回条数。"""
    payload = {
        "app": "ORBYTE",
        "version": __version__,
        "exported_at": todo_store.now_iso(),
        "note": "生活待办只存本机，这份文件是你换电脑/卸载前的手动备份。",
        "todos": todo_store.life_export(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(payload["todos"])


def open_path(target: str | Path) -> None:
    """用系统默认程序打开文件/文件夹（导出后让用户直接看到）。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        logger.warning("打开失败 %s：%s", target, exc)
