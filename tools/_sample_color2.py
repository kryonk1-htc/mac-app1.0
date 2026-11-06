"""第二轮：把徽章 / 分隔线 / 图标色采准（排除白底干扰）。"""

from collections import Counter

from PIL import Image

IM = Image.open(
    r"C:\Users\Lenovo\.workbuddy\clipboard-images\clipboard-2026-11-03T09-34-42-275Z-0a39ec2a.png"
).convert("RGB")


def hexof(c):
    return "#%02X%02X%02X" % c


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def nonwhite(box, label, thresh=250):
    x0, y0, x1, y1 = box
    cnt = Counter()
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = IM.getpixel((x, y))
            if min(c) < thresh:
                cnt[c] += 1
    for c, n in cnt.most_common(3):
        print(f"{label:<14} {hexof(c)}  n={n}")
    print()


def most_sat(box, label, kind="red"):
    x0, y0, x1, y1 = box
    best = None
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = IM.getpixel((x, y))
            if kind == "red":
                s = c[0] - max(c[1], c[2])
            elif kind == "green":
                s = c[1] - max(c[0], c[2])
            else:
                s = c[0] + c[1] - 2 * c[2]
            if best is None or s > best[1]:
                best = (c, s)
    print(f"{label:<14} {hexof(best[0])} (sat={best[1]})")


# 徽章：整片扫描非白众数
nonwhite((48, 205, 105, 245), "P1 chip bg")
nonwhite((48, 345, 105, 385), "P1-b chip bg")
nonwhite((48, 420, 105, 460), "P2 chip bg")
nonwhite((48, 505, 105, 545), "P2-b chip bg")
nonwhite((48, 630, 105, 672), "P3 chip bg")

most_sat((52, 210, 100, 240), "P1 text", "red")
most_sat((52, 425, 100, 455), "P2 text", "warm")
most_sat((52, 635, 100, 665), "P3 text", "green")

# 分隔线：取中段 200..500，找最暗行
print("\n-- divider scan --")
for y in range(270, 284):
    row = [IM.getpixel((x, y)) for x in range(200, 500)]
    mn = min(row, key=lum)
    avg = tuple(sum(c[i] for c in row) // len(row) for i in range(3))
    print(f"  y={y} min={hexof(mn)} avg={hexof(avg)}")

# 底部大分隔线（查看全部任务与生活待办之间）
print("\n-- divider2 --")
for y in range(730, 750):
    row = [IM.getpixel((x, y)) for x in range(200, 500)]
    mn = min(row, key=lum)
    if lum(mn) < 245:
        print(f"  y={y} min={hexof(mn)}")

# 图标色（标题栏星标 / 齿轮 / 关闭）
most_sat((480, 40, 660, 80), "header icons", "warm")
nonwhite((480, 40, 660, 80), "header icon px", 200)

# 分组箭头
nonwhite((20, 105, 50, 135), "chevron", 200)

# 底栏时钟图标
nonwhite((28, 818, 55, 850), "clock icon", 200)

# 状态按钮边框（竖向切面 x 处）
print("\n-- status border --")
for x in range(518, 528):
    print(f"  x={x} {hexof(IM.getpixel((x, 187)))}")
for x in range(648, 660):
    print(f"  x={x} {hexof(IM.getpixel((x, 187)))}")

# 下拉浮层内部：选中行绿底 + 勾
nonwhite((535, 215, 700, 262), "menu sel bg", 252)

# 生活待办 chevron 与文字
nonwhite((20, 755, 60, 790), "life chevron", 200)
