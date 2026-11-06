# ORBYTE —— 桌面待办插件

日清应用（智能协作工作台）的 Windows 桌面伴侣。常驻托盘 + 悬浮球，本地 SQLite 为主库，**不开账号也能单独用**，连上账号后工作待办自动续传。

## 三条硬约束

| 约束 | 落地方式 |
|---|---|
| 离线可用 | 所有操作先写本地 SQLite，网络不在交互路径上 |
| 断点续传 | outbox 队列 + 服务端幂等 + `acked_upto` 确认机制 |
| 生活待办不出本机 | 写入层不排进 outbox、发送层二次过滤、服务端再拒一次 |
| 开箱即用 | 服务器地址后台配好（内置常量 / 打包注入），首启不弹任何输入框 |

完整设计见 `../docs/桌面插件-安装与接入方案-2026-10.md`。

## 目录

```
ORBYTE/
├── build.ps1                 一键打包单文件 exe（调用 orbyte.spec）
├── orbyte.spec               打包描述（体积控制在这里，见下方"构建"）
├── app.py                    入口（薄壳，实际逻辑在 src/orbyte）
├── src/orbyte/
│   ├── _build_info.py        编译期注入常量（打包时临时改写，打完还原）
│   ├── config.py             配置与令牌；BUILTIN_SERVER 在这里
│   ├── db.py                 SQLite 建表/迁移（版本迁移 + 缺列自愈）
│   ├── todo_store.py         待办读写 + 项目分组 + 工时结算 + outbox 排队
│   ├── sync.py               后台同步线程（推 + 拉，退避重试）
│   ├── pairing.py            6 位码配对的插件侧
│   ├── api_client.py         HTTP（绕开系统代理）
│   ├── system.py             开机自启 / orbyte:// 协议 / 生活待办导出
│   ├── updater.py            检查更新（只提示，不静默替换）
│   └── ui/                   全部 Qt 原生绘制，无浏览器内核
│       ├── theme.py          双主题令牌 + 全局 QSS + 圆角卡片 / 公共小控件
│       ├── orb.py            悬浮球（主入口：点开/收起挂件，右键菜单）
│       ├── widget.py         挂件（前 5 条未完成 / 单行 + 右侧状态圈 / 生活折叠）
│       ├── settings.py       挂件设置浮层（主题 / 置顶 / 跳转）
│       ├── window.py         主窗口（全部待办 / 项目增删改 / 详情翻页）
│       ├── detail.py         详情页（状态 / 优先级 / 截止 / 计时 / 同项目切换）
│       ├── project_dialog.py 新建·重命名项目
│       ├── pair_dialog.py    6 位配对码
│       ├── prompt.py         统一外观的提示框
│       └── tray.py           托盘菜单
├── tools/
│   ├── selftest_core.py      本地库自测（项目/工时/生活隔离/导出/升级）
│   ├── selftest_sync.py      同步自测（推送/拉取/冲突，假服务端）
│   └── preview_screens.py    界面截图（真实窗口渲染，浅色+深色各一套）
├── release/                  发布产物（git 忽略）
└── docs/
    ├── sync-protocol.md      同步接口契约
    └── screens/              界面截图
```

## 界面

| 截图 | 是什么 |
|---|---|
| `docs/screens/orb.png` | 悬浮球：桌面上的常驻入口 |
| `docs/screens/widget-with-orb.png` | 真实形态：球在右下，点开挂件贴着它左边出来 |
| `docs/screens/widget-light.png` / `widget-dark.png` | 挂件：一行一条，右侧一个状态圈（浅色 / 深色） |
| `docs/screens/widget-done-light.png` / `widget-done-dark.png` | 完成态：灰圈打勾 + 整行删除线，0.7 秒后移出列表 |
| `docs/screens/widget-life-light.png` / `widget-life-dark.png` | 生活待办展开（区域内部滚动） |
| `docs/screens/widget-settings-light.png` | 设置浮层：主题 / 置顶 / 跳转 |
| `docs/screens/window-light.png` / `window-dark.png` | 全部待办：项目分组、状态点、标题、优先级徽标 |
| `docs/screens/window-detail-dark.png` | 主窗口里的详情页（原地翻页，不弹新窗） |

**视觉口径**：浅色 `#FFFFFF` 底 / `#F4F6F8` 次级面 / `#172033` 文字 / `#E5E9EF` 分隔 /
赤陶橙 `#C2701B` 作主色；深色 `#15191F` / `#222730` / `#F1F3F5` / `#303742` /
`#E0A46A`。正文统一 16px，挂件标题 17px，优先级徽标 14px。**界面里不允许出现
小于 14px 的字**——桌面端不是网页，125% / 150% 缩放下 12px 会糊成一团。

主色和语义色是分开的：`primary` 只用在意"可点的主元素"（选中页签、主按钮、聚焦边框），
`green` 只表示"已完成 / 成功"。两者合并会让详情页「进行中」和「已完成」变成同一个颜色。

主题用 `theme.apply("light" | "dark" | "system")` 切换，用户选择存在 `ui_theme`。
**所有模块引用颜色必须写 `theme.C_TEXT2`，不许 `from .theme import C_TEXT2`**——
后者是启动那一刻值的拷贝，切主题时不会变。

## 构建

**前提：本机 Python 3.12 + PyQt5 + PyInstaller**（不打 MSI、不需要管理员权限、目标机不用装 Python）

```powershell
cd ORBYTE
.\build.ps1                                  # 用 config.py 里的 BUILTIN_SERVER
.\build.ps1 -ServerUrl "https://xxx.example.com"   # 把地址编进 exe
```

产物：`release/ORBYTE-<version>.exe`（单文件，约 44 MB）+ `release/release-notes.txt`。
版本号唯一来源是 `src/orbyte/__init__.py` 的 `__version__`。

**体积在哪**：Qt 运行时占绝大多数，自己的业务代码只有 150KB 左右。

| 组成 | 约 |
|---|---|
| ICU 三件套（conda 版 Qt 硬依赖） | 30 MB |
| Qt5 Core/Gui/Widgets/Network | 18 MB |
| OpenSSL（Qt5Network 的 TLS 后端） | 8 MB |
| Python 运行时 | 7 MB |

`orbyte.spec` 里过滤 `a.binaries`，把 `qpdf` / `qtvirtualkeyboard` 插件顺带拖进来的
`Qt5Pdf / Qt5Quick / Qt5Qml / Qt5QmlModels / Qt5VirtualKeyboard` 全部剔掉——这几个
一共 13MB，`--exclude-module` 是拦不住它们的（DLL 依赖扫描不看 Python 模块名单）。

> 踩坑备忘：
> - 本机有 safe-delete 保护，脚本里不要 `Remove-Item build/dist`（会被拦并报 `SAFE_DELETE_FAIL_CLOSED`），`--clean --noconfirm` 已够用。
> - `_build_info.py` 打完必须还原，否则真实域名会被提交进 git。
> - **同步线程不许碰控件**：`SyncWorker` 是普通 `threading.Thread`，跨线程调 `set_sync_status()` 这类 Qt 方法会触发 `Qt5Core` 里的 0xc0000005，进程无声消失、日志戛然而止、没有任何 Python 栈（只有事件日志里一条 APPCRASH）。一律走 `main.SyncBridge` 的 `pyqtSignal` + `QueuedConnection`。
> - **QSS 会吃掉 `QFont.setStrikeOut`**：样式表里只要写了 `font-size`，polish 阶段会整份替换 widget 字体，删除线一起被丢，表现是"同屏有的行有、有的没有"。统一用 `theme.strike()`，它在 polish/show/styleChange 时会重贴一次。
> - **QSS 用 `string.Template` 拼**（`$token`）而不是 f-string：QSS 满屏大括号，f-string 要写成 `{{ }}`，漏一个就从那里开始整段失效（上一版"按钮全变纯文字"就是这个）。
> - **状态入口不再是菜单**：挂件里点圈循环三态（未开始 → 进行中 → 完成）。曾经用自绘 `_StateMenu` 做三选一，删掉下拉箭头之后弹菜单反而多一层，一并去掉了。主窗口/详情页仍是点圆点循环 + 详情页三态按钮。
> - **截图的窗口会闪**：`tools/preview_screens.py` 必须真实 `show()`（Windows 的 offscreen 平台没有字体引擎，截出来全是空白字），所以每次跑会在桌面上闪 13 次窗口。改完多处再跑，别改一处跑一次。
> - **迁移按版本分组**：`_MIGRATIONS` 是 `(版本号, 语句组)`，不要用语句下标当版本号——一版多条语句时下标会错位，升级过半的老库会把后面的 ALTER 永久跳过。`_ensure_columns()` 还会按 `PRAGMA` 实测结果补列，版本号写脏了也能自愈。
> - 原生崩溃没有 Python 栈，靠 `app.py` 里的 `faulthandler` 落到 `%LOCALAPPDATA%\ORBYTE\crash.log`。

## 发布到服务器

1. 把 `release/ORBYTE-<version>.exe` 与 `release-notes.txt` 放到服务器 `ORBYTE/release/`（或 `.env` 的 `ORBYTE_RELEASE_DIR`）
2. 后端 `GET /api/orbyte/release` 现扫该目录，版本/大小/sha256 现算，下载地址由服务端拼
3. 应用内「头像菜单 → ORBYTE 待办」即可下载

**不需要改代码**——目录里放新 exe 就生效。

## 服务器地址怎么定

客户端按以下优先级取值（`config.resolve_server`），**永远有值，所以不会弹输入框**：

1. 命令行 `--server https://…`（临时调试）
2. `%APPDATA%\ORBYTE\config.json` 的 `server`（配对后自动写入）
3. 编译期注入 `-ServerUrl`（`BUILD_SERVER_URL`）
4. 内置常量 `config.BUILTIN_SERVER`

**测试阶段**：第 4 级是 `http://127.0.0.1:3000`（本机 Nest），直接双击 exe 就能连本机联调。
**部署之后**：把 `config.py` 的 `BUILTIN_SERVER` 改成正式域名重新打包（或用 `-ServerUrl`），不要改用户机器上的配置。

## 连接账号（可选）

不连也能用，数据只在本机。想同步时点面板上的「连接账号，开始同步」→ 面板显示 6 位配对码 → 点按钮开浏览器 → 登录后点「允许接入」→ 插件轮询到 active 即完成。

- 配对码 10 分钟有效，确认后服务端立即清空
- 令牌只存 `dtk_<48hex>` 明文于本机 SQLite，服务端只存 sha256
- 撤销设备：应用内「头像菜单 → ORBYTE 待办 → 撤销」，插件下次请求收到 401 自动回到未连接状态
- **本机侧的两个出口**（托盘右键菜单）：已连接时显示「重新配对…」与「断开本机账号…」。
  服务端判 401（设备被撤销 / 设备记录不存在）时插件会**自动清空本地令牌并弹出配对窗**，
  不清令牌的话「连接账号」会被"已经连接"挡住，用户没有任何回到配对的路。
