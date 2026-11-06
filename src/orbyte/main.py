"""ORBYTE 入口：挂件（主界面）+ 全部待办窗口 + 悬浮球 + 后台同步。

启动顺序有讲究：
1. 单实例互斥（第二次双击只是把已有的叫到前面来，绝不开两个进程抢同一个 SQLite）
2. 日志 / 目录 / 建表 / 恢复 inflight（必须在同步线程之前）
3. 同步线程（没配对时它只安静待着，不做网络请求）
4. 界面（悬浮球先出来，用户立刻能用）

首启不弹任何引导：本机就能记，配对是"想同步才做"的可选项。
托盘只保留图标（无菜单）：左键单击切换挂件；所有功能入口收敛到
悬浮球右键菜单和主窗口顶栏。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import sys
import webbrowser

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from . import __version__, config, system, todo_store
from .api_client import ApiClient
from .db import database
from .log import setup_logging
from .pairing import POLL_INTERVAL_SEC, PairingSession, token_hash
from .paths import APP_NAME, DATA_DIR, ICON_FILE, ensure_dirs
from .sync import SyncWorker
from .ui import prompt, theme
from .ui.pair_dialog import PairDialog
from .ui.tray import Tray
from .ui.widget import Widget
from .ui.window import MainWindow
from .updater import UpdateChecker

logger = logging.getLogger("orb.main")

_MUTEX_NAME = "Global\\ORBYTE-Desktop-SingleInstance"


class SyncBridge(QObject):
    """同步线程 → GUI 线程的单向信号桥。

    同步线程是普通 threading.Thread，不是 QThread。在那边直接调
    widget.set_sync_status() 属于跨线程操作 Qt 控件，Windows 上的表现是
    Qt5Core 里 0xc0000005 访问违例：进程毫无征兆消失、日志戛然而止、
    连 Python 栈都没有（只有事件日志里一条 APPCRASH）。
    所以状态统一 emit 出来，用 QueuedConnection 排队回主线程再动界面。
    """

    status = pyqtSignal(dict)



def single_instance() -> tuple[bool, object]:
    """单实例互斥。返回 (是否首个实例, 句柄)。

    Windows 用命名 Mutex；macOS / Linux 用文件锁——`ctypes` 在非 Windows 上
    没有 `windll` 这个属性，原来那句会让进程在建库和 QApplication 之前
    直接 AttributeError 退出（console=False 下连 traceback 都看不到）。

    第二个返回值必须被调用方一直持有到进程结束：文件对象一被回收，
    flock 就自动释放了，单实例就失效。
    """
    if sys.platform.startswith("win"):
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        return (not already), handle

    import fcntl

    handle = open(DATA_DIR / "orbyte.lock", "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False, None
    return True, handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbyte", description="ORBYTE 桌面待办插件")
    parser.add_argument("--server", help="服务器地址，如 https://work.example.com")
    parser.add_argument("--window", action="store_true", help="启动即打开全部待办")
    parser.add_argument("--no-orb", action="store_true", help="不显示桌面悬浮球")
    parser.add_argument("--version", action="store_true")
    # orbyte:// 协议会把 URL 当第一个参数传进来，这里吸收掉
    args, _rest = parser.parse_known_args(argv)

    if args.version:
        print(__version__)
        return 0

    setup_logging()
    ensure_dirs()
    logger.info("ORBYTE %s 启动，数据目录 %s", __version__, DATA_DIR)

    first, _mutex = single_instance()
    if not first:
        logger.info("已有实例在运行，退出本次启动")
        return 0

    database.init_schema()

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))

    # 主题要在任何窗口构造之前落地：窗口构造时会去读 theme.C_XXX
    theme.apply(config.get_theme())

    # 服务器地址由后台配置（cli > config.json > 编译注入 > 内置常量），
    # 永远有值，所以这里不再弹输入框——用户拿到 exe 不该被问"服务器是什么"
    server, source = config.resolve_server(args.server)
    logger.info("服务器地址：%s（来源 %s）", server, source)

    # ---- 系统集成（失败不影响主流程）----

    system.register_protocol()
    if not config.get_flag("autostart_init"):
        config.set_flag("autostart_init", True)
        if getattr(sys, "frozen", False):
            system.set_autostart(True)

    # ---- 同步 ----

    def client_factory() -> ApiClient | None:
        token = config.get_token()
        if not token:
            return None
        return ApiClient(server, token, timeout=12)

    def save_pos(key: str):
        return lambda x, y: config.save_window_pos(key, x, y)

    widget = Widget(
        on_moved=save_pos("widget"),
        on_resized=lambda w, h: config.save_window_size("widget", w, h),
        size=config.get_window_size("widget"),
        pinned=config.get_flag("widget_pin"),
    )
    window = MainWindow(on_moved=save_pos("window"))

    if sys.platform == "darwin":
        # macOS 习惯：点 Dock 图标要能把窗口叫回来。主窗口关掉之后，
        # 除了悬浮球就没有别的入口了，这一步是"找回全部待办"的兜底。
        # 用事件过滤器而不是子类化 QApplication，不改现有 app 对象的类型。
        class _DockActivateFilter(QObject):
            def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
                if event.type() == QEvent.ApplicationActivate and not window.isVisible():
                    window.show()
                    window.raise_()
                    window.activateWindow()
                return False

        _dock_filter = _DockActivateFilter()
        app.installEventFilter(_dock_filter)

    last_pulled = {"n": 0}

    def on_sync_status(payload: dict) -> None:
        widget.set_sync_status(payload)
        window.set_sync_status(payload)
        if orb is not None:
            orb.set_sync_state(str(payload.get("state") or "idle"))
        pulled = int(payload.get("pulled") or 0)
        if pulled != last_pulled["n"]:
            last_pulled["n"] = pulled
            # 拉到别人的改动：列表要重画，否则要等下次打开才看到
            widget.refresh()
            if not window.isHidden():
                window.refresh()
        # 令牌刚被判定失效（同步线程已清空本地令牌）：主动把配对流程拉起来。
        # 只信 relogin 这个一次性标记——同步线程只在第一次 401 时置真，
        # 否则每 30 秒一次的状态广播会把配对窗反复弹出来。
        if payload.get("relogin") and not state["pairing"]:
            # 排队到下一轮事件循环再弹：此刻还在处理同步信号，
            # 直接开模态对话框会把当前这一轮界面刷新卡住。
            QTimer.singleShot(0, begin_pairing)

    # 同步线程的状态一律经信号排队回主线程再动界面（详见 SyncBridge 注释）
    bridge = SyncBridge()
    bridge.status.connect(on_sync_status, Qt.QueuedConnection)

    worker = SyncWorker(client_factory, on_status=bridge.status.emit)

    # ---- 界面装配 ----

    # 悬浮球是产品的主入口：装上就该在桌面右下角看到一个球，点它展开待办。
    # 之前把球降级成 --orb 调试开关、改成常驻挂件，等于把入口藏了。
    from .ui.orb import Orb

    orb = Orb(autostart=system.autostart_enabled()) if not args.no_orb else None
    if orb is not None:
        orb.start_animation()

    # 托盘只留图标做"总能找回"的兜底。Windows 无右键菜单、左键单击切换挂件；
    # macOS 必须有菜单（否则点击无反应），多两项入口走下面这两个信号。
    tray = Tray(app)

    def _show_main_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    tray.requestWindow.connect(_show_main_window)
    tray.requestQuit.connect(app.quit)

    def place_orb() -> None:
        if orb is None:
            return
        saved = config.get_window_pos("orb")
        if saved:
            orb.move(*saved)
            return
        screen = orb.screen().availableGeometry()
        # 底部那个 90 是 Windows 任务栏高度的经验值。mac 上 availableGeometry
        # 已经扣掉菜单栏和 Dock，只需要留一点呼吸位，否则球会飘到半空。
        reserve = 90 if sys.platform.startswith("win") else 24
        orb.move(
            screen.right() - orb.width() - 10,
            screen.bottom() - orb.height() - reserve,
        )

    def place_widget() -> None:
        """挂件贴着球展开：球在右，面板在它左边；左边放不下就翻到球上方。

        面板是"球的一部分"，每次展开都贴着球出现，用户不用去找它飘到哪了。
        """
        if orb is None:
            saved = config.get_window_pos("widget")
            if saved:
                widget.move(*saved)
                return
            screen = widget.screen().availableGeometry()
            # 按实际高度贴右下角；以前写死 -430，面板改高改矮就会露在屏幕外
            widget.move(
                screen.right() - widget.width() - 28,
                screen.bottom() - widget.height() - 20,
            )
            return

        ball = orb.frameGeometry()
        screen = orb.screen().availableGeometry()
        gap = 12
        x = ball.left() - widget.width() - gap
        y = ball.center().y() - widget.height() // 2
        if x < screen.left() + 8:
            # 左侧没地方了：挪到球的正上方，水平居中于球
            x = ball.center().x() - widget.width() // 2
            y = ball.top() - widget.height() - gap
        x = min(max(x, screen.left() + 8), screen.right() - widget.width() - 8)
        y = min(max(y, screen.top() + 8), screen.bottom() - widget.height() - 8)
        widget.move(x, y)

    def place_window() -> None:
        saved = config.get_window_pos("window")
        if saved:
            window.move(*saved)
            return
        screen = window.screen().availableGeometry()
        window.move(
            screen.center().x() - window.width() // 2,
            screen.top() + max(20, (screen.height() - window.height()) // 2),
        )

    place_orb()
    place_window()

    def show_widget() -> None:
        # 三张界面（挂件/列表/详情）同一时刻只留一张：挂件展开时收起主窗口
        if not window.isHidden():
            window.hide()
        # 先按内容把高度收敛，再贴球定位——否则高度还停在旧值，位置会跳一下
        widget.refresh()
        place_widget()
        widget.show()
        widget.raise_()

    def hide_widget() -> None:
        widget.hide()

    def toggle_widget() -> None:
        if widget.isVisible():
            hide_widget()
        else:
            show_widget()

    def show_window() -> None:
        hide_widget()
        window.show_list()
        window.show()
        window.raise_()
        window.activateWindow()

    def collapse_to_widget() -> None:
        window.hide()
        show_widget()

    def show_window_at(item_uuid: str) -> None:
        """挂件里点任务名：收起面板，在主窗口直接打开这条的详情。

        设计说明写的是"点击任务名称定位对应任务"——挂件只有 360 宽，
        塞不下详情表单，展开成主窗口才是对的动作。
        """
        hide_widget()
        window.show_list()
        window.show()
        window.show_detail(item_uuid)
        window.raise_()
        window.activateWindow()

    def refresh_all() -> None:
        widget.refresh()
        if not window.isHidden():
            window.refresh()
        worker.trigger()

    widget.changed.connect(refresh_all)
    window.changed.connect(refresh_all)
    # 同步状态由 on_sync_status 负责，这里只接"要立刻推一次"的动作
    widget.expandRequested.connect(show_window)
    window.collapseRequested.connect(collapse_to_widget)
    widget.closeRequested.connect(hide_widget)
    widget.connectRequested.connect(lambda: begin_pairing())
    widget.openDetailRequested.connect(show_window_at)
    # 挂件标题栏「手动同步」：立刻推+拉一轮，状态广播会带回界面刷新
    widget.refreshSyncRequested.connect(worker.trigger)

    def on_theme_changed(_mode: str) -> None:
        # 主题一变，主窗口里那些内联颜色的行必须重建，否则一半新一半旧
        if not window.isHidden():
            window.refresh()
        if not widget.isHidden():
            widget.refresh()
        window.update()

    theme.subscribe(on_theme_changed)

    def on_theme_picked(mode: str) -> None:
        """球菜单「主题切换」：落库并应用（界面刷新由 subscribe 回调兜住）。"""
        config.set_theme(mode)
        theme.apply(mode)

    if orb is not None:
        orb.clicked.connect(toggle_widget)
        orb.requestPanel.connect(show_window)
        orb.requestExit.connect(app.quit)
        # 主题切换已从设置弹窗挪到球右键菜单（2026-11-04 用户拍板）
        orb.themePicked.connect(on_theme_picked)

        def on_orb_moved(x: int, y: int) -> None:
            config.save_window_pos("orb", x, y)
            # 球挪了位置，展开着的面板要跟着挪，否则两者会分离到屏幕两端
            if widget.isVisible():
                place_widget()

        orb.moved.connect(on_orb_moved)

    # 托盘左键单击 = 球单击：叫出/收起挂件
    tray.toggleWidget.connect(toggle_widget)

    # ---- 配对 ----

    def begin_pairing(auto_open: bool = False) -> None:
        """用户主动点「连接账号」或令牌失效后自动进入这里。

        `auto_open=True` 只在"服务端判定令牌失效、刚刚自动弹出本窗"时传：
        那是用户没主动要求、但必须马上处理的情况，顺手把授权页打开；
        用户自己点菜单进来的仍是老规则——绝不替他开浏览器。
        """
        if config.get_token():
            prompt.info(
                window if window.isVisible() else widget,
                "已经连接",
                f"这台设备已授权：<b>{config.get_device_name()}</b>。<br>"
                "想换账号，先断开本机授权再重新连接。",
            )
            return
        if state["pairing"]:
            return
        state["pairing"] = True
        dialog = PairDialog(window if window.isVisible() else widget)
        dialog.openRequested.connect(lambda url: webbrowser.open(url))
        session_box.clear()

        def cleanup() -> None:
            pair_timer.stop()
            state["pairing"] = False

        dialog.finished.connect(lambda _r: cleanup())
        start_pairing(dialog, auto_open=auto_open)
        pair_timer.start()
        dialog.exec_()

    state = {"pairing": False}
    session_box: dict[str, PairingSession] = {}

    def start_pairing(dialog: PairDialog, auto_open: bool = False) -> None:
        client = ApiClient(server, None)
        session = PairingSession(client, __version__)
        session_box["session"] = session
        try:
            session.start()
        except Exception as exc:  # noqa: BLE001 - 网络问题不该弹崩溃框
            logger.warning("获取配对码失败：%s", exc)
            dialog.set_error(
                f"连不上 {server}。<br>待办只存本机不受影响，稍后再试或联系管理员核对地址。"
            )
            return
        dialog.set_code(session.code, session.confirm_url)
        if auto_open and session.confirm_url:
            # 只在这一种场景自动开浏览器：旧令牌已被服务端判死，
            # 用户此刻多半不知道要做什么，替他把授权页打开最省事。
            webbrowser.open(session.confirm_url)

    pair_timer = QTimer()
    pair_timer.setInterval(POLL_INTERVAL_SEC * 1000)

    def poll_pairing() -> None:
        session = session_box.get("session")
        if not session:
            return
        try:
            status = session.poll()
        except Exception:  # noqa: BLE001
            return
        if status == "active":
            pair_timer.stop()
            state["pairing"] = False
            logger.info("配对成功，开始同步")
            # 用户拍板：首次配对成功后默认开启开机自启（之后想关只能改注册表
            # 或卸载，产品里不再暴露这个开关）
            if getattr(sys, "frozen", False) and not system.autostart_enabled():
                if system.set_autostart(True):
                    logger.info("已默认开启开机自启")
            prompt.info(
                window if window.isVisible() else widget,
                "已连接",
                "这台电脑已连上日清应用，工作待办开始同步。",
            )
            widget.refresh()
            window.refresh()
            worker.trigger()
            for child in app.topLevelWidgets():
                if isinstance(child, PairDialog) and child.isVisible():
                    child.accept()

    pair_timer.timeout.connect(poll_pairing)

    def on_link_toggle() -> None:
        """主窗口顶栏「一键连接 / 断开」。

        未连接 → 直接进配对流程（首次配对才跳网页授权）；
        已连接 → 确认后清掉本机令牌，待办回到只存本机。
        """
        if config.get_token():
            target = window if window.isVisible() else widget
            if not prompt.ask(
                target,
                "断开本机账号",
                f"断开后待办只存本机、不再同步到云端。<br>"
                f"当前设备：<b>{config.get_device_name()}</b>。",
                "断开",
                "取消",
            ):
                return
            config.clear_token()
            config.clear_pending_token()
            logger.info("已断开本机账号")
            widget.refresh()
            window.refresh()
            worker.trigger()
            return
        begin_pairing()

    window.linkToggleRequested.connect(on_link_toggle)

    # ---- 检查更新 ----

    checker = UpdateChecker(client_factory, __version__)

    def on_update_found(info: dict) -> None:
        notes = (info.get("notes") or "").strip()
        body = (
            f"当前版本 v{__version__}，服务端最新 <b>v{info.get('version')}</b>。"
            + (f"<br><br>{notes}" if notes else "")
        )
        target = window if window.isVisible() else widget
        if prompt.ask(target, "有新版本", body, "去下载", "以后再说"):
            urls = info.get("url") or ""
            if urls.startswith("/"):
                urls = f"{server}{urls}"
            webbrowser.open(urls)

    def on_update_none() -> None:
        logger.info("检查更新：已是最新版本")

    def on_update_failed(message: str) -> None:
        logger.info("检查更新失败：%s", message)

    checker.found.connect(on_update_found)
    checker.upToDate.connect(on_update_none)
    checker.failed.connect(on_update_failed)

    # ---- 球右键菜单的新信号 ----

    def export_life() -> None:
        """生活待办导出成 JSON 文件（换机/卸载前的兜底，只读）。"""
        from PyQt5.QtWidgets import QFileDialog

        rows = todo_store.life_export()
        if not rows:
            prompt.info(
                window if window.isVisible() else widget,
                "没有可导出的内容",
                "生活待办还是空的，先记几条再来导出。",
            )
            return
        path, _f = QFileDialog.getSaveFileName(
            window if window.isVisible() else widget,
            "导出生活待办",
            "orbyte-life.json",
            "JSON (*.json)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        prompt.info(
            window if window.isVisible() else widget,
            "已导出",
            f"共 {len(rows)} 条，已保存到：<b>{path}</b>",
        )

    def on_autostart_toggled(enabled: bool) -> None:
        ok = system.set_autostart(bool(enabled))
        # 写注册表失败时把勾选拨回真实值，别让菜单骗人
        if orb is not None:
            orb.set_autostart(enabled if ok else system.autostart_enabled())
        logger.info("开机自启%s", "已开启" if ok and enabled else "已关闭" if ok else "设置失败")

    if orb is not None:
        orb.exportLife.connect(export_life)
        orb.checkUpdate.connect(checker.check_async)
        orb.autostartToggled.connect(on_autostart_toggled)

    # ---- 起服务 ----

    def claim_pending() -> None:
        """启动认领：上次在网页点了「允许」，但插件在那之前就被关掉了。

        `PairingSession.start()` 会把令牌先写进 pending 槽位，注释里写着
        「万一用户点完允许时插件被关掉，重启后还能认领」——认领这一步原先
        并没有实现，于是用户会卡在「网页已允许、插件仍显示未连接」，
        只能重新出码再点一次。这里补上：启动时拿 pending 令牌问一次状态，
        已 active 就扶正并立刻触发同步。

        网络不通/服务端没响应一律静默跳过，认领失败不该影响启动。
        """
        if config.get_token():
            return
        pending = config.get_pending_token()
        if not pending:
            return
        try:
            result = ApiClient(server, None).pair_poll(
                code=None, token_hash=token_hash(pending)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("启动认领失败：%s", exc)
            return
        if str(result.get("status") or "") != "active":
            return
        config.promote_pending()
        logger.info("启动认领：设备已授权，令牌已生效")
        worker.trigger()
        widget.refresh()
        window.refresh()

    worker.start()
    on_sync_status({"state": "idle", "pending": todo_store.pending_count()})
    # 延后一点再认领：先让界面出来，别在启动路径上做网络请求
    QTimer.singleShot(1200, claim_pending)

    # 启动 5 秒后静默查一次更新（不抢启动速度，也不打扰）
    if config.get_token():
        QTimer.singleShot(5000, checker.check_async)

    def on_quit() -> None:
        logger.info("收到退出请求")
        worker.stop()
        pair_timer.stop()
        tray.hide()
        if orb is not None:
            orb.close()
        database.close()
        app.quit()

    app.aboutToQuit.connect(lambda: logger.info("ORBYTE 已退出"))

    # 有球就显示球；没球（--no-orb / 极少数环境）才退回常驻挂件
    if orb is not None:
        orb.show()
        # 首次启动自动展开一次面板：让用户知道"点这个球会出来东西"，
        # 之后就一直安静待在右下角，不再自己弹
        if not config.get_flag("orb_hint_shown") and not args.window:
            config.set_flag("orb_hint_shown", True)
            QTimer.singleShot(600, show_widget)

    if args.window:
        QTimer.singleShot(200, show_window)
    elif orb is None:
        QTimer.singleShot(120, lambda: (show_widget(), window.refresh()))

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
