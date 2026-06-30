#!/usr/bin/env python3
"""
從純色遮罩（_segmented_clean.png）提取每個食物的真實輪廓座標。

讀取 _color_map.json 取得每種食物的精確 RGB 值，
對每個顏色做精確像素匹配，再跑 findContours 提取輪廓。
"""

import cv2
import numpy as np
import matplotlib.pyplot as plt
import json
import sys
from pathlib import Path

# ── 參數 ──────────────────────────────────────────────────────────────────────
BASE        = "/home/bl515-01/sam3/90"          # 不含副檔名的前綴
EPSILON     = 4.0                                # approxPolyDP 精度（px）
COLOR_TOL   = 8                                  # RGB 精確匹配容差（0-255）
MIN_AREA    = 3000                               # 忽略面積小於此的輪廓

# 依前綴找對應檔案
CLEAN_IMAGE = BASE + "_segmented_clean.png"
COLOR_MAP   = BASE + "_color_map.json"
OUTPUT_IMG  = BASE + "_contours_real.png"
OUTPUT_JSON = BASE + "_contours.json"

# ── 載入 ──────────────────────────────────────────────────────────────────────
img_bgr = cv2.imread(CLEAN_IMAGE)
img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
H, W    = img_bgr.shape[:2]
print(f"純色遮罩大小: {W} x {H} px")

with open(COLOR_MAP) as f:
    color_map = json.load(f)   # {food_name: [R, G, B]}

print(f"讀到 {len(color_map)} 種食物的顏色對應表:")
for name, rgb in color_map.items():
    print(f"  {name:12s} -> RGB{tuple(rgb)}")
print()

# ── 建立輸出圖 ────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(14, 12))
ax.imshow(img_rgb)
ax.set_title("Real Contour Polygons (exact color match)", fontsize=13)
ax.axis('off')

kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

all_contours = {}   # food_name -> [[polygon_pts], ...]

# ── 對每種食物：精確顏色比對 → 輪廓 → 座標 ───────────────────────────────────
for food_name, rgb in color_map.items():
    r, g, b = rgb

    # 精確像素匹配（允許容差）
    lower = np.array([max(0,r-COLOR_TOL), max(0,g-COLOR_TOL), max(0,b-COLOR_TOL)], dtype=np.uint8)
    upper = np.array([min(255,r+COLOR_TOL), min(255,g+COLOR_TOL), min(255,b+COLOR_TOL)], dtype=np.uint8)
    mask  = cv2.inRange(img_rgb, lower, upper)

    if mask.sum() == 0:
        print(f"[{food_name:12s}] 未找到匹配像素（RGB{tuple(rgb)}），跳過")
        continue

    # 形態學清理
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)

    # 找輪廓
    contours_raw, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    valid = [c for c in contours_raw if cv2.contourArea(c) >= MIN_AREA]

    if not valid:
        print(f"[{food_name:12s}] 找到像素但無有效輪廓（面積 < {MIN_AREA}px²）")
        continue

    food_polygons = []
    total_area    = 0

    # 選個顯示顏色（用原始 RGB 轉 hex）
    hex_color = f"#{r:02x}{g:02x}{b:02x}"

    for idx, cnt in enumerate(valid):
        area   = cv2.contourArea(cnt)
        total_area += area

        # 簡化多邊形 → 這裡就是真實像素座標
        approx = cv2.approxPolyDP(cnt, epsilon=EPSILON, closed=True)
        pts    = approx.squeeze()

        if pts.ndim == 1:
            pts = pts.reshape(1, 2)
        if len(pts) < 3:
            continue

        food_polygons.append(pts.tolist())

        # 畫多邊形（用真實座標）
        poly_patch = plt.Polygon(pts, fill=False, edgecolor=hex_color, linewidth=2.5, zorder=3)
        ax.add_patch(poly_patch)

        # 畫頂點（小點）
        ax.scatter(pts[:, 0], pts[:, 1], color=hex_color, s=14, zorder=5)

        # 標食物名稱（最大輪廓才標）
        if idx == 0:
            cx = pts[:, 0].mean()
            cy = pts[:, 1].mean()
            ax.text(cx, cy,
                    f"{food_name}\n{len(pts)} pts\n{area:.0f}px²",
                    fontsize=8.5, color='white', ha='center', va='center',
                    fontweight='bold',
                    bbox=dict(facecolor=hex_color, alpha=0.85, edgecolor='none', pad=3),
                    zorder=6)

    if not food_polygons:
        continue

    all_contours[food_name] = food_polygons

    # 輸出座標到終端機
    print(f"[{food_name}]  {len(food_polygons)} 個輪廓，總面積 {total_area:.0f}px²，顏色 RGB{tuple(rgb)}")
    for i, poly in enumerate(food_polygons):
        arr = np.array(poly)
        print(f"  輪廓{i}: {len(poly)} 頂點  X:{arr[:,0].min()}~{arr[:,0].max()}  Y:{arr[:,1].min()}~{arr[:,1].max()}")
        print(f"    前5點: {poly[:5]}")

# ── 存 JSON ───────────────────────────────────────────────────────────────────
with open(OUTPUT_JSON, "w") as f:
    json.dump(all_contours, f, indent=2)

plt.tight_layout()
plt.savefig(OUTPUT_IMG, dpi=150, bbox_inches='tight')
print(f"\n✓ 座標 JSON -> {OUTPUT_JSON}")
print(f"✓ 視覺化圖  -> {OUTPUT_IMG}")
