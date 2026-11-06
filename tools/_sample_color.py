"""从设计稿截图里采样精确色值：底色取众数，文字取最暗/最亮色。"""

from collections import Counter

from PIL import Image

IM = Image.open(
    r"C:\Users\Lenovo\.workbuddy\clipboard-images\clipboard-2026-11-03T09-34-42-275Z-0a39ec2a.png"
).convert("RGB")

W, H = IM.size


def hexof(c):
    return "#%02X%02X%02X" % c


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def dom(box, label, skip=1.0):
    """众数色（跳过边距）"""
    x0, y0, x1, y1 = box
    cnt = Counter()
    for y in range(y0, y1):
        for x in range(x0, x1):
            cnt[IM.getpixel((x, y))] += 1
    top = cnt.most_common(3)
    print(f"{label:<16} dom={hexof(top[0][0])} ({top[0][1]})  "
          f"next={[hexof(c) + f'({n})' for c, n in top[1:]]}")


def dark(box, label, pct=0.02):
    """最暗 2% 像素的平均色 ≈ 文字色"""
    x0, y0, x1, y1 = box
    px = [IM.getpixel((x, y)) for y in range(y0, y1) for x in range(x0, x1)]
    px.sort(key=lum)
    n = max(1, int(len(px) * pct))
    sel = px[:n]
    avg = tuple(sum(c[i] for c in sel) // len(sel) for i in range(3))
    print(f"{label:<16} text={hexof(avg)}   min={hexof(px[0])}")


print(f"image {W}x{H}")

# 面板
dom((300, 120, 700, 140), "panel-bg")
dom((20, 300, 40, 400), "panel-left-edge")

# 标题「待办事项」
dark((30, 40, 160, 75), "title")

# 分组「工作 · 前5项」
dark((70, 105, 210, 135), "group-head")

# 任务标题
dark((45, 170, 430, 200), "task-title")
# 项目后缀「- ToDoList」
dark((340, 170, 445, 200), "task-project")

# 分隔线：y≈268 附近找最暗的一行
for y in range(260, 280):
    row = [IM.getpixel((x, y)) for x in range(30, 660)]
    avg = tuple(sum(c[i] for c in row) // len(row) for i in range(3))
    if avg != (255, 255, 255):
        print(f"divider y={y}  {hexof(avg)}")

# 状态按钮（未开始）
dom((520, 175, 590, 200), "status-btn-bg")
# 按钮边框：找按钮左右边缘
for x in range(510, 530):
    print(f"  status edge x={x}: {hexof(IM.getpixel((x, 187)))}")
dark((525, 175, 585, 200), "status-btn-text")

# 优先级徽章底色
dom((52, 210, 95, 240), "p1-bg")
dark((52, 210, 95, 240), "p1-fg", pct=0.10)
dom((52, 420, 95, 450), "p2-bg")
dark((52, 420, 95, 450), "p2-fg", pct=0.10)
dom((52, 632, 95, 662), "p3-bg")
dark((52, 632, 95, 662), "p3-fg", pct=0.10)

# 下拉浮层
dom((640, 215, 700, 260), "menu-sel-bg")
dom((600, 300, 700, 320), "menu-bg")
dark((555, 268, 640, 292), "menu-item-text", pct=0.05)
# 勾的颜色：绿色最饱和像素
best = None
for y in range(222, 255):
    for x in range(540, 570):
        c = IM.getpixel((x, y))
        if c[1] > c[0] + 30 and c[1] > c[2] + 20:
            sat = c[1] - min(c[0], c[2])
            if best is None or sat > best[1]:
                best = (c, sat)
print(f"check-green      {hexof(best[0]) if best else 'n/a'}")

# 查看全部任务
dark((250, 690, 430, 725), "view-all")

# 底栏「工时采集：已暂停」
dark((55, 818, 260, 850), "footer")

# 浮层阴影边界（浮层左侧）
for x in range(505, 530):
    print(f"  menu shadow x={x}: {hexof(IM.getpixel((x, 250)))}")
