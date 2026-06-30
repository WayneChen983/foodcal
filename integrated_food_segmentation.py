import os
import re
import ast
import json
import torch
import gc
import sys
import glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── 設定 (結合兩者邏輯) ────────────────────────────────────────────────────────
MODEL_ID   = "Qwen/Qwen2.5-VL-7B-Instruct"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
SAM3_ROOT  = "/home/bl515-01/Grounding-DINO/sam3"
BASE_DIR   = "/home/bl515-01/Grounding-DINO/foodpic"
IMAGES_DIR = os.path.join(BASE_DIR, "original")
BBOX_DIR   = os.path.join(BASE_DIR, "qwen_bbox")
MASK_DIR   = os.path.join(BASE_DIR, "sam3_mask")

# 輸出目錄初始化
os.makedirs(BBOX_DIR, exist_ok=True)
os.makedirs(MASK_DIR, exist_ok=True)

# 顏色池
COLORS_HEX = [
    "#FF4444", "#44AAFF", "#44FF44", "#FF9900",
    "#CC44FF", "#00CCCC", "#FF44AA", "#AACC00",
    "#FF6622", "#2244FF", "#66FF22", "#FF2299",
]

COLORS_RGB = [
    (255, 68, 68),   # 紅
    (68, 170, 255),  # 藍
    (68, 255, 68),   # 綠
    (255, 153, 0),   # 橙
    (204, 68, 255),  # 紫
    (0, 204, 204),   # 青
    (255, 68, 170),  # 粉紅
    (170, 204, 0)    # 黃綠
]

# ── 工具函式 ──────────────────────────────────────────────────────────────────

def cleanup_gpu():
    """清理 GPU 記憶體，確保階段切換順暢"""
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    print("[INFO] GPU memory cleaned.")

def build_prompt(img_w, img_h):
    return f"""\
Carefully analyze the image and identify EVERY distinct food item present.

The image is {img_w} pixels wide and {img_h} pixels tall.
- x ranges from 0 (left) to {img_w} (right)
- y ranges from 0 (top) to {img_h} (bottom)

For each UNIQUE type of food, provide exactly ONE bounding box (the best one).

Return ONLY a valid Python dictionary, nothing else. Format:
{{"food_name": [x1, y1, x2, y2], "another_food": [x1, y1, x2, y2]}}

Where x1,y1 = top-left corner, x2,y2 = bottom-right corner, all in pixels."""

def xyxy_to_cxcywh_norm(x1, y1, x2, y2, img_w, img_h):
    cx = (x1 + x2) / 2 / img_w
    cy = (y1 + y2) / 2 / img_h
    w  = (x2 - x1) / img_w
    h  = (y2 - y1) / img_h
    cx = max(0.0, min(1.0, cx))
    cy = max(0.0, min(1.0, cy))
    w  = max(0.0, min(1.0, w))
    h  = max(0.0, min(1.0, h))
    return [cx, cy, w, h]

def mask_to_pil(mask_tensor):
    arr = mask_tensor.squeeze().cpu().numpy()
    arr = (arr > 0.5).astype(np.uint8) * 255
    return Image.fromarray(arr, mode="L")

def overlay_mask_on_rgba(orig_rgba: Image.Image, mask_pil: Image.Image,
                         color=(255, 100, 100), alpha=0.45):
    mask_arr  = np.array(mask_pil)
    overlay   = np.zeros((*mask_arr.shape, 4), dtype=np.uint8)
    overlay[mask_arr > 0] = (*color, int(alpha * 255))
    overlay_img = Image.fromarray(overlay, mode="RGBA")
    return Image.alpha_composite(orig_rgba, overlay_img)

# ── 階段 1：偵測 (Qwen2.5-VL) ─────────────────────────────────────────────────

def detection_stage(images):
    print(f"\n[Stage 1] Loading {MODEL_ID} ...")
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    from qwen_vl_utils import process_vision_info

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        device_map="auto",
    )
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    print("Qwen model loaded.")

    for img_path in images:
        if not os.path.exists(img_path):
            print(f"  [SKIP] {img_path} not found.")
            continue

        print(f"  Processing BBox: {img_path}")
        image = Image.open(img_path).convert("RGB")
        W, H  = image.size

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": f"file://{os.path.abspath(img_path)}"},
                    {"type": "text",  "text": build_prompt(W, H)},
                ],
            }
        ]

        text_input = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(
            text=[text_input],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(DEVICE)

        with torch.no_grad():
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

        # 解析 dict
        food_boxes = {}
        try:
            match = re.search(r"\{.*\}", raw_output, re.DOTALL)
            if match:
                clean = match.group(0).replace("null", "None").replace("true", "True").replace("false", "False")
                parsed = ast.literal_eval(clean)
                if isinstance(parsed, dict):
                    for k, vals in parsed.items():
                        if isinstance(vals, (list, tuple)) and len(vals) == 4:
                            name = str(k).strip().lower()
                            x1, y1, x2, y2 = [int(v) for v in vals]
                            x1, x2 = max(0, min(x1, W)), max(0, min(x2, W))
                            y1, y2 = max(0, min(y1, H)), max(0, min(y2, H))
                            if x2 > x1 and y2 > y1:
                                food_boxes[name] = [x1, y1, x2, y2]
        except Exception as e:
            print(f"    [WARN] Parse error: {e}")

        # 畫框與儲存
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except Exception:
            font = ImageFont.load_default()

        for i, (label, (x1, y1, x2, y2)) in enumerate(food_boxes.items()):
            color = COLORS_HEX[i % len(COLORS_HEX)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
            # Label background
            text_size = font.getbbox(label) if hasattr(font, 'getbbox') else (0, 0, len(label)*12, 22)
            draw.rectangle([x1, y1 - 28, x1 + (text_size[2]-text_size[0]) + 4, y1], fill=color)
            draw.text((x1 + 3, y1 - 26), label, fill="white", font=font)

        basename = os.path.splitext(os.path.basename(img_path))[0]
        out_jpg  = os.path.join(BBOX_DIR, f"{basename}_qwen_bboxes.jpg")
        image.save(out_jpg)

        json_data = {
            "image":  os.path.abspath(img_path),
            "width":  W,
            "height": H,
            "bboxes": food_boxes,
        }
        out_json = os.path.join(BBOX_DIR, f"{basename}_qwen_bboxes.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"    Saved BBox JSON -> {out_json}")

    # 釋放資源
    del model
    del processor
    cleanup_gpu()

# ── 階段 2：分割 (SAM3) ────────────────────────────────────────────────────────

def segmentation_stage():
    print("\n[Stage 2] Loading SAM3 model...")
    if SAM3_ROOT not in sys.path:
        sys.path.insert(0, SAM3_ROOT)

    from sam3 import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    # BPE 檔案尋找
    _bpe_candidates = [
        os.path.join(SAM3_ROOT, "sam3", "assets", "bpe_simple_vocab_16e6.txt.gz"),
        os.path.join(SAM3_ROOT, "assets", "bpe_simple_vocab_16e6.txt.gz"),
    ]
    bpe_path = next((p for p in _bpe_candidates if os.path.exists(p)), None)
    if bpe_path is None:
        raise FileNotFoundError("Could not find bpe_simple_vocab_16e6.txt.gz in SAM3 assets.")

    # 加速設定
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    
    # 建立模型
    print(f"  Building SAM3 model on {DEVICE}...")
    model = build_sam3_image_model(bpe_path=bpe_path)

    model.to(DEVICE)
    model.eval()
    processor = Sam3Processor(model, confidence_threshold=0.3, device=DEVICE)
    print("SAM3 model loaded.")

    # 讀取偵測階段產出的所有 JSON
    json_files = sorted(glob.glob(os.path.join(BBOX_DIR, "*_qwen_bboxes.json")))
    
    for json_path in json_files:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        img_path = data["image"]
        img_w    = data["width"]
        img_h    = data["height"]
        bboxes   = data["bboxes"]

        if not os.path.exists(img_path):
            continue

        print(f"  Segmenting: {img_path}")
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        food_dir  = os.path.join(MASK_DIR, base_name)
        os.makedirs(food_dir, exist_ok=True)

        orig_img = Image.open(img_path).convert("RGB")
        
        # SAM3 的某些 fused 算子強制使用 bfloat16，因此推理必須在 autocast 下執行
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            inference_state = processor.set_image(orig_img)
            
        combined_rgba = orig_img.convert("RGBA")

        results_summary = {
            "image":  img_path,
            "width":  img_w,
            "height": img_h,
            "items":  []
        }

        for i, (label, (x1, y1, x2, y2)) in enumerate(bboxes.items()):
            color = COLORS_RGB[i % len(COLORS_RGB)]
            box_norm = xyxy_to_cxcywh_norm(x1, y1, x2, y2, img_w, img_h)

            processor.reset_all_prompts(inference_state)
            
            # 這裡執行分割，同樣需要 autocast
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                state = processor.add_geometric_prompt(
                    box=box_norm,
                    label=True,
                    state=inference_state,
                )

            masks  = state.get("masks")
            scores = state.get("scores")

            if masks is not None and masks.shape[0] > 0:
                best_idx = scores.argmax().item()
                best_mask = masks[best_idx, 0]
                best_score = scores[best_idx].item()

                mask_pil = mask_to_pil(best_mask)
                combined_rgba = overlay_mask_on_rgba(combined_rgba, mask_pil, color=color)

                results_summary["items"].append({
                    "label":       label,
                    "bbox_xyxy":   [x1, y1, x2, y2],
                    "bbox_cxcywh_norm": box_norm,
                    "score":       round(best_score, 4)
                })

        # 儲存結果
        final_output_path = os.path.join(food_dir, f"{base_name}_all_masks.jpg")
        combined_rgba.convert("RGB").save(final_output_path)
        
        result_json_path = os.path.join(MASK_DIR, f"{base_name}_results.json")
        with open(result_json_path, "w", encoding="utf-8") as f:
            json.dump(results_summary, f, ensure_ascii=False, indent=2)
        print(f"    Saved Segmentation JSON -> {result_json_path}")

    # 釋放資源
    del model
    del processor
    cleanup_gpu()

# ── 執行 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 定義要處理的圖片 (完全對齊使用者原始請求)
    target_images = [
        "/home/bl515-01/Grounding-DINO/foodpic/original/food1.jpg",
        "/home/bl515-01/Grounding-DINO/foodpic/original/food2.jpg",
        "/home/bl515-01/Grounding-DINO/foodpic/original/food3.jpg",
        "/home/bl515-01/Grounding-DINO/foodpic/original/food4.jpg",
    ]

    print("=== Integrated Food Segmentation Pipeline Start ===")
    
    # Stage 1
    detection_stage(target_images)
    
    # Stage 2
    segmentation_stage()
    
    print("\n=== Pipeline Completed Successfully ===")
