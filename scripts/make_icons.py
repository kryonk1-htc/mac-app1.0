"""把 assets/orbyte.ico 转成 macOS 需要的 png 与 icns。

在 macOS 构建机上由 workflow 调用：

    python scripts/make_icons.py

为什么需要这一步：
- 项目里只有 Windows 的 .ico；macOS 的 Dock、菜单栏、.app 图标都吃 png / icns；
- Qt for macOS 不保证带 qico 图像插件，直接 QIcon("x.ico") 可能得到空图标，
  而空图标不会报错，只表现为"托盘/窗口没图标"，很难查。

产物（写回 src/orbyte/assets/）：
- orbyte.png    512×512，运行时窗口与菜单栏图标用
- orbyte.icns   多尺寸，PyInstaller 打进 .app 用（仅 macOS 生成）

脚本失败不该阻断构建：workflow 里是 continue-on-error，
缺图标时 spec 会传 icon=None，用系统默认图标。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "orbyte" / "assets"
SRC = ASSETS / "orbyte.ico"

ICONSET = ROOT / "build" / "ORBYTE.iconset"

#: 文件名与 iconutil 的要求严格对应，少一个它就不认这个 iconset
SPECS = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def _largest_frame(ico: Image.Image) -> Image.Image:
    """取 ico 里最大的那一帧。PIL 默认给的是第一帧（常常只有 16×16）。"""
    try:
        sizes = sorted(ico.ico.sizes())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 不是多尺寸 ico 就按原样用
        sizes = [ico.size]
    ico.size = sizes[-1]
    ico.load()
    return ico.convert("RGBA")


def main() -> int:
    if not SRC.exists():
        print(f"找不到源图标：{SRC}")
        return 1

    base = _largest_frame(Image.open(SRC))
    print(f"源图标 {base.width}×{base.height}")

    png = ASSETS / "orbyte.png"
    base.resize((512, 512), Image.LANCZOS).save(png, "PNG")
    print(f"已生成 {png.name}（512×512）")

    if sys.platform != "darwin":
        print("非 macOS，跳过 icns（iconutil 只在 macOS 上有）")
        return 0

    ICONSET.mkdir(parents=True, exist_ok=True)
    for name, px in SPECS:
        base.resize((px, px), Image.LANCZOS).save(ICONSET / name, "PNG")

    icns = ASSETS / "orbyte.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(icns)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("iconutil 失败：", (result.stderr or "").strip())
        return 1

    print(f"已生成 {icns.name}（{icns.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
