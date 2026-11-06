# ORBYTE 打包脚本（在 Windows PowerShell 里跑）
#   .\build.ps1
#   .\build.ps1 -Python "C:\Python312\python.exe"
#   .\build.ps1 -ServerUrl "https://xxx.example.com"
#
# 产物：release\ORBYTE-<版本>.exe（单文件，目标机不需要装 Python）
#
# 打包描述在仓库根的 orbyte.spec 里，不在这个脚本里拼命令行——
# 体积大头是"Qt 插件把别的 Qt 模块拖进来"，只能用 spec 过滤
# a.binaries，--exclude-module 拦不住（详见 spec 顶部注释）。

param(
    [string]$Python = "C:\Users\Lenovo\anaconda3\python.exe",
    # 编译期注入的服务器地址（空 = 不注入，走内置 BUILTIN_SERVER）
    # 注意：变量名不要叫 $Version —— PowerShell 变量不区分大小写，
    # 会和下面从 __init__.py 解析出的 $version 撞车。
    [string]$ServerUrl = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

if (-not (Test-Path $Python)) {
    Write-Host "找不到 Python：$Python" -ForegroundColor Red
    exit 1
}

$spec = Join-Path $root "orbyte.spec"
if (-not (Test-Path $spec)) {
    Write-Host "找不到打包描述：$spec" -ForegroundColor Red
    exit 1
}

# ---- 版本号单一来源：src\orbyte\__init__.py ----
$initText = Get-Content "$root\src\orbyte\__init__.py" -Raw
if ($initText -match '__version__\s*=\s*"([^"]+)"') {
    $version = $Matches[1]
} else {
    Write-Host "读不到版本号" -ForegroundColor Red
    exit 1
}
Write-Host "打包 ORBYTE $version" -ForegroundColor Cyan

# ---- 清理 ----
# 不要在这里 Remove-Item build/dist：本机有 safe-delete 保护（删除走回收站），
# 对目录整体的删除会被拦下并抛 SAFE_DELETE_FAIL_CLOSED，脚本直接中断。
# PyInstaller 的 --clean --noconfirm 会自己清缓存并覆盖 dist 产物，足够用。
New-Item -ItemType Directory -Force -Path (Join-Path $root "release") | Out-Null

# ---- 编译期注入服务器地址 ----
# 源码要进 git，里面不能出现真实域名；所以打包前临时重写 _build_info.py，
# 打完在下面的 finally 里还原，避免注入值被误提交。
$buildInfo = Join-Path $root "src\orbyte\_build_info.py"
$buildInfoBackup = ""
$injected = $false
if ($ServerUrl -ne "") {
    $buildInfoBackup = [System.IO.File]::ReadAllText($buildInfo)
    $serverValue = $ServerUrl.Trim().TrimEnd("/")
    $injectedText = @"
from __future__ import annotations

BUILD_SERVER_URL = "$serverValue"

BUILD_VERSION = "$version"
"@
    [System.IO.File]::WriteAllText(
        $buildInfo, $injectedText, (New-Object System.Text.UTF8Encoding($false))
    )
    $injected = $true
    Write-Host "已注入 BUILD_SERVER_URL=$serverValue（打包后还原）" -ForegroundColor DarkGray
}

Write-Host "开始打包（首次约 1-3 分钟）…" -ForegroundColor Cyan
# PyInstaller 会往 stderr 打 INFO/WARNING；PS 5.1 在 Stop 模式下会把原生命令的
# stderr 当成异常抛出（NativeCommandError），所以这里临时降级，改用退出码判断。
# 整体包在 try/finally 里：无论成功、失败还是 Ctrl+C，_build_info.py 都必须还原。
$code = 1
try {
    $ErrorActionPreference = "Continue"
    & $Python -m PyInstaller --noconfirm --clean $spec
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
} finally {
    $ErrorActionPreference = "Stop"
    if ($injected) {
        [System.IO.File]::WriteAllText(
            $buildInfo, $buildInfoBackup, (New-Object System.Text.UTF8Encoding($false))
        )
        Write-Host "已还原 _build_info.py" -ForegroundColor DarkGray
    }
}
if ($code -ne 0) {
    Write-Host "PyInstaller 失败（退出码 $code）" -ForegroundColor Red
    exit $code
}

# ---- 产出到 release\ ----
$target = Join-Path $root "release\ORBYTE-$version.exe"
Move-Item (Join-Path $root "dist\ORBYTE.exe") $target -Force

# 发布说明跟着包一起走，服务端 /release 接口会读它
$notes = Join-Path $root "release-notes.txt"
if (Test-Path $notes) {
    Copy-Item $notes (Join-Path $root "release\release-notes.txt") -Force
}

$size = [math]::Round((Get-Item $target).Length / 1MB, 1)
Write-Host ""
Write-Host "完成：$target" -ForegroundColor Green
Write-Host "体积：$size MB" -ForegroundColor Green
Write-Host ""
Write-Host "把 release 目录里的 exe 放到服务器 ORBYTE/release/ 下，应用内即可下载。" -ForegroundColor DarkGray
