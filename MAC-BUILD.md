# ORBYTE macOS 版：怎么打出 .app

这份目录是 Windows 版 `ORBYTE/` 的副本，已经做过 macOS 适配。
Windows 的那套（`orbyte.spec` + `build.ps1`）原样保留，行为没有变化。

## 为什么要在 GitHub 上打包

PyInstaller **不能交叉编译**：Windows 上打不出 Mac 的 `.app`，必须在一台
macOS 机器上跑。GitLab.com 的 macOS runner 只对 Premium/Ultimate 开放，
而 GitHub **公共仓库的 macOS runner 完全免费**（不限量、不扣额度、不用绑卡）。

所以流程是：把这份代码上传到一个 GitHub 公共仓库 → 点一下按钮 → 下载产物。

⚠️ 公共仓库意味着**代码对外可见**。本目录已确认不含真实域名与密钥
（`_build_info.py` 的 `BUILD_SERVER_URL` 为空，`config.py` 的
`BUILTIN_SERVER` 是 `127.0.0.1:3000`）。打完包建议把仓库删掉。

## 步骤

### 1. 上传

在 GitHub 新建一个 **Public** 空仓库，然后把 **本目录的内容**（不是外层目录）
传上去，让仓库根就长这样：

```
.github/workflows/mac.yml
src/
app.py
orbyte-mac.spec
scripts/make_icons.py
...
```

传法：网页上 "Add file → Upload files"，把文件拖进去；或者用 git。

### 2. 配服务器地址

仓库 **Settings → Secrets and variables → Actions → New repository secret**

- Name：`ORBYTE_SERVER_URL`
- Value：正式地址，例如 `https://work.example.com`（**结尾不要带斜杠**）

不配也能构建成功，但产物会指向 `127.0.0.1:3000`，同事装上连不上服务器——
构建日志里会有 `::warning::` 提示。

### 3. 触发构建

仓库顶部 **Actions → 左侧 build-mac → 右侧 Run workflow → 绿色按钮**

约 5–10 分钟。构建日志里会打印 `Qt 5.15.x` 和 `已注入服务器地址: ...`，
确认这两行没问题再下载。

### 4. 下载

进这次运行的详情页，最底部 **Artifacts → ORBYTE-mac**（是个 zip）。

### 5. 同事安装

解压得到 `ORBYTE.app`，拖进「应用程序」。第一次打开会被 Gatekeeper 拦：

- 方法一：在 Finder 里**右键 → 打开 → 再点“打开”**
- 方法二：终端执行
  ```bash
  xattr -dr com.apple.quarantine /Applications/ORBYTE.app
  ```

之后正常双击即可。数据落在 `~/Library/Application Support/ORBYTE/`。

## 这次改了什么

| 文件 | 改动 |
|---|---|
| `src/orbyte/main.py` | 单实例互斥：Windows 用命名 Mutex，mac 用文件锁（原来的 `ctypes.windll` 在 mac 上会直接崩）；球的初始位置不再用 Windows 任务栏的 90px 魔数；mac 上点 Dock 图标能把主窗口叫回来 |
| `src/orbyte/system.py` | 开机自启 / `orbyte://` 协议在 mac 上是空实现（协议改由 Info.plist 声明）；打开文件用 `open` 而不是 `xdg-open` |
| `src/orbyte/paths.py` | 数据目录改到 `~/Library/Application Support/ORBYTE`；图标按平台选 png / ico |
| `src/orbyte/ui/theme.py` | 窗口旗标按平台分：mac 没有 `WindowStaysOnBottomHint`，且 `Qt.Tool` 窗口在 app 失活时会被隐藏，必须配 `WA_MacAlwaysShowToolWindow`；mac 跳过 `setMask`（会丢系统阴影）；字体栈换成 PingFang |
| `src/orbyte/ui/orb.py` | 悬浮球在 mac 上 `Tool + StaysOnTop + WA_MacAlwaysShowToolWindow`，保证切到别的应用后球还在；菜单里不显示「开机自启」 |
| `src/orbyte/ui/window.py` | 主窗口在 mac 上是真正的 `Qt.Window`（进 Dock，可被 Dock 唤回） |
| `src/orbyte/ui/tray.py` | mac 菜单栏图标必须挂 QMenu，否则点击毫无反应；菜单有「显示/隐藏挂件 / 全部待办 / 退出」 |
| `orbyte-mac.spec`（新增） | EXE + COLLECT + BUNDLE 三段式出 `.app`；不做 `a.binaries` 过滤（Windows 那套按 `.dll` 名删的规则会误删 mac 的 `libqcocoa.dylib`）；Info.plist 注册 `orbyte://` |
| `scripts/make_icons.py`（新增） | `orbyte.ico` → `orbyte.png` + `orbyte.icns` |
| `.github/workflows/mac.yml`（新增） | 手动触发的 mac 构建 |

## 可能出问题的地方

| 现象 | 原因 / 处理 |
|---|---|
| `pip install PyQt5` 报找不到 wheel | 把版本钉死重试：`pip install PyQt5==5.15.9`；再不行把 `runs-on` 换成 `macos-13`（Intel） |
| 构建成功但打开就闪退 | 大概率是 Qt 平台插件没打进包。把 `orbyte-mac.spec` 里 `console=False` 临时改成 `True` 重打一次，让同事在终端里跑 `./ORBYTE.app/Contents/MacOS/ORBYTE` 看报错 |
| 悬浮球切个应用就不见了 | `WA_MacAlwaysShowToolWindow` 在某些系统版本上失效。备选：去掉 `Qt.Tool`，改用 `Qt.Window + WindowStaysOnTopHint`（代价是 Dock 里多一个图标） |
| 菜单栏没有图标 | `make_icons.py` 失败时没有 png，回落到自绘圆点。看构建日志里那一步有没有报错 |
| 连不上服务器 | `ORBYTE_SERVER_URL` 没配，或值里带了结尾斜杠。看构建日志的「已注入服务器地址」那一行 |

## 本地（Windows）验证过的部分

- `python -m compileall src app.py scripts` 全部通过
- 所有模块可正常 import（`orbyte.paths / system / ui.*`）
- `sys.platform` 模拟成 `darwin` 后：数据目录切到 `Library/Application Support`、
  `autostart_enabled` / `set_autostart` 返回 False、`register_protocol` 不抛异常
- Windows 下 `DATA_DIR` 仍是 `%LOCALAPPDATA%\ORBYTE`、图标仍选 `.ico`（行为未变）
- `scripts/make_icons.py` 在 Windows 上能生成 `orbyte.png`，跳过 icns

**没法本地验证的**：窗口旗标的实际观感、菜单栏图标、Dock 行为——这些只能
在 CI 产物上由使用 Mac 的人确认，可能要来回调 1–2 轮。
