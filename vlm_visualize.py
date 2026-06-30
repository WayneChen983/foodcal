"""
vlm_visualize.py
================
視覺化 Qwen2.5-VL 如何找到食物目標並用 bounding box 框住。
用法：
    python3 vlm_visualize.py 2001.jpg
    python3 vlm_visualize.py 2001.jpg --save          # 另存 PNG
    python3 vlm_visualize.py 2001.jpg --no-display    # 只存不顯示
"""

import os
import sys
import ast
import re
import argparse
import time
import gc

import torch
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch
from PIL import Image
from transformers import (
    Qwen3VLMoeForConditionalGeneration,
    AutoProcessor,
    BitsAndBytesConfig,
)
from qwen_vl_utils import process_vision_info


# ── 固定高對比色盤 ────────────────────────────────────────────────
PALETTE = [
    "#FF4444",  # 紅
    "#44AAFF",  # 藍
    "#FFCC00",  # 黃
    "#44DD44",  # 綠
    "#FF8800",  # 橙
    "#CC44FF",  # 紫
    "#00DDDD",  # 青
    "#FF66BB",  # 粉紅
    "#99DD00",  # 黃綠
    "#00AA66",  # 深綠
    "#FF44AA",  # 梅紅
    "#DDAA00",  # 棕黃
]


def hex_to_rgb_float(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def wait_for_gpu(min_free_gb: float = 10.0, timeout_s: int = 1200):
    """等到 GPU 有足夠空閒記憶體且無衝突 process 才繼續。"""
    import subprocess
    if not torch.cuda.is_available():
        return
    my_pid = os.getpid()
    print(f"[GPU] Waiting until {min_free_gb:.1f} GB free and no competing processes...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        free, total = torch.cuda.mem_get_info()
        free_gb = free / 1024**3
        # 取得目前佔用 GPU 的 process
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory",
                 "--format=csv,noheader"],
                text=True
            ).strip()
            other_procs = [
                line for line in out.splitlines()
                if line.strip() and int(line.split(',')[0].strip()) != my_pid
            ]
        except Exception:
            other_procs = []

        print(f"[GPU] Free: {free_gb:.1f}/{total/1024**3:.1f} GB  |  Other GPU procs: {len(other_procs)}", flush=True)
        if free_gb >= min_free_gb and len(other_procs) == 0:
            print(f"[GPU] ✓ Ready — {free_gb:.1f} GB free, no competing processes.")
            return
        if other_procs:
            print(f"[GPU] Waiting for PID(s): {[p.split(',')[0].strip() for p in other_procs]}")
        print("[GPU] Sleeping 20s...")
        time.sleep(20)
    print("[WARN] GPU wait timeout — proceeding anyway.")


def run_vlm(image_path: str, wait: bool = False) -> tuple[dict, str]:
    """
    用 Qwen2.5-VL-7B 偵測食物，回傳 (food_boxes, raw_output)。
    food_boxes = {food_name: [x1, y1, x2, y2]}
    """
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[VLM] Device: {device}")

    # ── 等待 GPU 空閒（可選）─────────────────────────────────────────
    if wait:
        wait_for_gpu(min_free_gb=7.0)
    elif device == "cuda":
        free_mem, total_mem = torch.cuda.mem_get_info()
        free_gb = free_mem / 1024**3
        print(f"[VLM] GPU memory: {free_gb:.1f} GB free / {total_mem/1024**3:.1f} GB total")
        if free_gb < 5.0:
            print(f"[WARN] Only {free_gb:.1f} GB free — consider using --wait flag.")

    print("[VLM] Loading Qwen3-VL-30B-A3B-Instruct (4-bit)...")
    t0 = time.time()
    q4_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
    )
    # 8-bit + CPU offload 備用（當 4-bit GPU 不夠時）
    q8_cpu_config = BitsAndBytesConfig(
        load_in_8bit=True,
        llm_int8_enable_fp32_cpu_offload=True,
    )
    try:
        model = Qwen3VLMoeForConditionalGeneration.from_pretrained(
            "Qwen/Qwen3-VL-30B-A3B-Instruct",
            torch_dtype="auto",
            device_map=device,
            quantization_config=q4_config,
        )
        print("[VLM] Loaded with 4-bit quantization.")
    except torch.OutOfMemoryError:
        print("[WARN] GPU OOM with 4-bit. Retrying with 8-bit + CPU offload...")
        gc.collect()
        torch.cuda.empty_cache()
        model = Qwen3VLMoeForConditionalGeneration.from_pretrained(
            "Qwen/Qwen3-VL-30B-A3B-Instruct",
            torch_dtype=torch.float32,
            device_map="auto",
            quantization_config=q8_cpu_config,
        )
        print("[VLM] Loaded with 8-bit + CPU offload.")
    processor = AutoProcessor.from_pretrained("Qwen/Qwen3-VL-30B-A3B-Instruct")
    print(f"[VLM] Model loaded in {time.time()-t0:.1f}s")

    # Qwen2.5-VL 的原生座標系統是 0~1000 的正規化空間
    # 明確要求輸出此格式，之後再縮放到真實像素
    img_w, img_h = Image.open(image_path).size
    prompt_text = f"""\
Carefully analyze the image and find EVERY SINGLE distinct food item on the plate.

CONTEXT: This is a Taiwanese buffet (自助餐/便當) style meal. Plates typically contain multiple separate dishes that may touch or slightly overlap.
Expect items like:
- Base: white rice, brown rice, noodles.
- Main Proteins: pork chop, chicken leg, braised pork, fish, tofu, braised egg, fried egg.
- Vegetables/Sides: cabbage, bok choy, broccoli, corn, stir-fried greens, carrots, kelp, bean sprouts.

CRITICAL RULES:
1. YOU MUST FIND THE REFERENCE CARD. There is a card (usually below or near the plate). It is MANDATORY to include "card" with its bounding box in the output.
2. Do not miss any food! Even if foods look similar in color or touch each other (like two different green vegetables, or sauce over rice), you MUST list them as separate distinct items.
3. Provide a concise, descriptive English name for each item (e.g., "white_rice", "braised_pork", "stir_fried_cabbage", "card").

CRITICAL: The image dimensions are exactly {img_w} pixels wide by {img_h} pixels tall.
- x goes from 0 (left edge) to {img_w} (right edge)
- y goes from 0 (top edge) to {img_h} (bottom edge)

Return ONLY a valid Python dictionary where:
- Each KEY is the English food name (lowercase, e.g. "rice", "broccoli", "pork")
- Each VALUE is a list [x1, y1, x2, y2] with the precise bounding box PIXEL coordinates.

x1, y1 is the TOP-LEFT corner of the item.
x2, y2 is the BOTTOM-RIGHT corner of the item.

Example: {{"rice": [150, 400, 450, 600], "broccoli": [100, 100, 300, 250], "card": [250, 750, 700, 980]}}

Return ONLY the Python dictionary. No other text."""

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": f"file://{os.path.abspath(image_path)}"},
                {"type": "text", "text": prompt_text},
            ],
        }
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(device)

    print("[VLM] Running inference...")
    t1 = time.time()
    generated_ids = model.generate(**inputs, max_new_tokens=512)
    generated_ids_trimmed = [
        out_ids[len(in_ids):]
        for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    raw_output = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]
    print(f"[VLM] Inference done in {time.time()-t1:.1f}s")
    print(f"\n{'='*60}")
    print("VLM Raw Output:")
    print(raw_output)
    print("=" * 60)

    # ── 解析 dict ──────────────────────────────────────────────────
    food_boxes = {}
    try:
        match = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if match:
            clean = (
                match.group(0)
                .replace("null", "None")
                .replace("true", "True")
                .replace("false", "False")
            )
            parsed = ast.literal_eval(clean)
            if isinstance(parsed, dict):
                for k, vals in parsed.items():
                    if not (isinstance(vals, (list, tuple)) and len(vals) == 4):
                        continue
                    name = str(k).strip().lower()
                    x1, y1, x2, y2 = [int(v) for v in vals]
                    # clamp 到圖片範圍
                    x1, x2 = max(0, x1), min(img_w, x2)
                    y1, y2 = max(0, y1), min(img_h, y2)
                    # 如果 x1 >= x2 或者 y1 >= y2 表示錯的框，修正一下
                    if x1 >= x2: x2 = x1 + 10
                    if y1 >= y2: y2 = y1 + 10
                    food_boxes[name] = [x1, y1, x2, y2]
    except Exception as e:
        print(f"[WARN] Failed to parse VLM output: {e}")

    print("\n[COORD] Pixel coordinates:")
    for name, pixel in food_boxes.items():
        print(f"  {name:20s}  px={pixel}")

    # 清理 GPU
    del model, processor, inputs
    gc.collect()
    torch.cuda.empty_cache()
    print("[VLM] GPU memory released.")

    return food_boxes, raw_output


def visualize(image_path: str, food_boxes: dict, save: bool, no_display: bool):
    """
    畫出原圖 + 每個食物的 bounding box（含名稱標籤）。
    左邊：原圖
    右邊：標有 bbox 的圖
    下方：VLM 偵測摘要表格
    """
    img = Image.open(image_path).convert("RGB")
    img_np = np.array(img)
    H, W = img_np.shape[:2]

    fig = plt.figure(figsize=(18, 9), facecolor="#1a1a2e")
    fig.suptitle(
        "Qwen2.5-VL  ·  Food Detection Visualization",
        fontsize=18, fontweight="bold", color="white", y=0.97,
    )

    # ── 左圖：原始圖 ───────────────────────────────────────────────
    ax_orig = fig.add_axes([0.02, 0.12, 0.44, 0.80])
    ax_orig.imshow(img_np)
    ax_orig.set_title("Original Image", color="white", fontsize=13, pad=8)
    ax_orig.axis("off")

    # ── 右圖：bbox 視覺化 ──────────────────────────────────────────
    ax_bbox = fig.add_axes([0.50, 0.12, 0.48, 0.80])
    ax_bbox.imshow(img_np)
    ax_bbox.set_title("VLM Bounding Boxes", color="white", fontsize=13, pad=8)
    ax_bbox.axis("off")

    n = len(food_boxes)
    legend_items = []

    for idx, (food, coords) in enumerate(food_boxes.items()):
        if len(coords) != 4:
            continue
        x1, y1, x2, y2 = coords
        color_hex = PALETTE[idx % len(PALETTE)]
        color_rgb = hex_to_rgb_float(color_hex)
        bw, bh = x2 - x1, y2 - y1

        # ── 外框（圓角矩形）────────────────────────────────────────
        rect = FancyBboxPatch(
            (x1, y1), bw, bh,
            boxstyle="round,pad=3",
            linewidth=2.5,
            edgecolor=color_hex,
            facecolor=(*color_rgb, 0.12),   # 半透明填色
        )
        ax_bbox.add_patch(rect)

        # ── 角標（左上角的小方塊標籤）────────────────────────────────
        label = food.upper()
        ax_bbox.text(
            x1 + 4, y1 + 4,
            f" {label} ",
            fontsize=9, fontweight="bold",
            color="white",
            va="top", ha="left",
            bbox=dict(
                facecolor=color_hex, alpha=0.85,
                edgecolor="none", pad=2,
                boxstyle="round,pad=0.3",
            ),
        )

        # ── 座標標籤（右下角，稍小）──────────────────────────────────
        coord_text = f"({x1},{y1})→({x2},{y2})"
        ax_bbox.text(
            x2 - 4, y2 - 4,
            coord_text,
            fontsize=7, color=color_hex,
            va="bottom", ha="right",
            bbox=dict(facecolor="#1a1a2e", alpha=0.6, edgecolor="none", pad=1),
        )

        # ── 對角線輔助線（讓視線聚焦 bbox 中心）─────────────────────
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ax_bbox.plot(
            [x1, x2], [y1, y2],
            linestyle="--", linewidth=0.6,
            color=color_hex, alpha=0.35,
        )
        ax_bbox.plot(cx, cy, "o", markersize=4,
                     color=color_hex, alpha=0.7)

        legend_items.append((color_hex, food, x1, y1, x2, y2, bw * bh))

    # ── 圖像尺寸標示 ────────────────────────────────────────────────
    ax_bbox.text(
        5, H - 5, f"Image: {W}×{H} px",
        fontsize=8, color="lightgray", va="bottom",
    )

    # ── 底部摘要表格 ────────────────────────────────────────────────
    ax_table = fig.add_axes([0.02, 0.02, 0.96, 0.09])
    ax_table.axis("off")

    if legend_items:
        col_labels = ["#", "Food", "x1", "y1", "x2", "y2", "Area (px²)"]
        table_data = []
        for i, (chex, fname, x1, y1, x2, y2, area) in enumerate(legend_items):
            table_data.append([
                str(i + 1), fname.capitalize(),
                str(x1), str(y1), str(x2), str(y2),
                f"{int(area):,}",
            ])

        tbl = ax_table.table(
            cellText=table_data,
            colLabels=col_labels,
            cellLoc="center",
            loc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.4)

        # 表頭樣式
        for col in range(len(col_labels)):
            tbl[(0, col)].set_facecolor("#16213e")
            tbl[(0, col)].set_text_props(color="white", fontweight="bold")

        # 資料格樣式
        for row in range(1, len(table_data) + 1):
            chex = legend_items[row - 1][0]
            crgb = hex_to_rgb_float(chex)
            tbl[(row, 0)].set_facecolor((*crgb, 0.4))
            for col in range(len(col_labels)):
                tbl[(row, col)].set_facecolor("#0f3460" if row % 2 == 0 else "#16213e")
                tbl[(row, col)].set_text_props(color="white")
    else:
        ax_table.text(
            0.5, 0.5, "⚠  No bounding boxes detected.",
            ha="center", va="center",
            color="orange", fontsize=13,
        )

    # ── 儲存 / 顯示 ─────────────────────────────────────────────────
    if save:
        base = os.path.splitext(image_path)[0]
        out_path = base + "_vlm_bbox_visualization.png"
        fig.savefig(out_path, dpi=150, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        print(f"\n[SAVE] Visualization saved → {out_path}")

    if not no_display:
        plt.show()

    plt.close("all")


def main():
    parser = argparse.ArgumentParser(description="Qwen2.5-VL Bounding Box Visualizer")
    parser.add_argument("image", help="Input image path")
    parser.add_argument("--save", action="store_true",
                        help="Save visualization as PNG")
    parser.add_argument("--no-display", action="store_true",
                        help="Do not open matplotlib window")
    parser.add_argument("--wait", action="store_true",
                        help="Wait until GPU has >=7GB free before loading model")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"[ERROR] Image not found: {args.image}")
        sys.exit(1)

    # 預設：有存檔 flag 就存，若沒有任何 flag 就顯示 + 存
    save = args.save or args.no_display  # no-display 情況下一定要存
    if not args.save and not args.no_display:
        save = True  # 預設都存一份

    print(f"\n{'='*60}")
    print(f"  Qwen2.5-VL Food Detection Visualizer")
    print(f"  Image : {args.image}")
    print(f"{'='*60}")

    food_boxes, raw_output = run_vlm(args.image, wait=args.wait)

    print(f"\n[RESULT] Detected {len(food_boxes)} item(s):")
    for name, coords in food_boxes.items():
        print(f"  {name:20s} → {coords}")

    if not food_boxes:
        print("[WARN] VLM returned no valid bounding boxes.")

    visualize(args.image, food_boxes, save=save, no_display=args.no_display)


if __name__ == "__main__":
    main()
