"""按行列密度定位红框矩形与白卡片矩形。"""
from PIL import Image

im = Image.open(r"C:\Users\Lenovo\.workbuddy\clipboard-images\clipboard-2026-11-04T01-11-09-680Z-248d60ed.png").convert("RGB")
w, h = im.size
px = im.load()

red = [[0] * w for _ in range(h)]
white = [[0] * w for _ in range(h)]
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        if r > 185 and g < 95 and b < 95:
            red[y][x] = 1
        elif r > 246 and g > 246 and b > 246:
            white[y][x] = 1

def dense_range(grid, axis, thresh):
    # axis=0: 按列统计; axis=1: 按行统计
    n = w if axis == 0 else h
    counts = []
    for i in range(n):
        c = 0
        if axis == 0:
            for y in range(h):
                c += grid[y][i]
        else:
            for x in range(w):
                c += grid[i][x]
        counts.append(c)
    idx = [i for i, c in enumerate(counts) if c >= thresh]
    return (min(idx), max(idx)) if idx else None

# 红框边框线是长直线：一行/列上应出现 >150 个红像素
rcols = dense_range(red, 0, 120)
rrows = dense_range(red, 1, 120)
print(f"red frame  cols {rcols} rows {rrows}  -> {(rcols[1]-rcols[0])} x {(rrows[1]-rrows[0])}")

# 白卡片：整行/列大面积白（卡片内部），阈值高一些
wcols = dense_range(white, 0, 150)
wrows = dense_range(white, 1, 150)
print(f"white card cols {wcols} rows {wrows}  -> {(wcols[1]-wcols[0])} x {(wrows[1]-wrows[0])}")
print(f"scale = {(rcols[1]-rcols[0])/(wcols[1]-wcols[0]):.3f}")
