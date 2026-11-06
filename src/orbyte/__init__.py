"""ORBYTE 桌面待办插件。

分层：
    paths / log        基础设施（路径、日志）
    db / todo_store    本地主库（SQLite）
    api_client / sync  与服务端通信、断点续传
    pairing            设备码配对
    ui/*               悬浮球、挂件面板、主窗口、双主题、托盘

（早期那套 WebView2 + 本地 HTTP 桥的方案已经废弃，界面全部是 Qt 原生绘制，
对应的 bridge.py / webview2.py 不再进安装包。）
"""

from __future__ import annotations

#: 与 release 包名、网页端展示保持一致；发版时只改这里
__version__ = "1.0.0"
