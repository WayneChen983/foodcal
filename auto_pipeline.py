import os
import gc
import re
import ast
import json
import torch
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image

def cleanup_gpu():
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    print("[INFO] GPU memory cleaned.")

def get_food_boxes(image_path):
    print("\n[Stage 1] Analyzing image with VLM (with bounding boxes)...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

    print(f"[VLM] Loading {MODEL_ID} ...")
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    from qwen_vl_utils import process_vision_info

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        device_map="auto",
    )
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    print("Qwen model loaded.")

    image = Image.open(image_path).convert("RGB")
    W, H = image.size

    prompt_text = f"""\
Carefully analyze the image and identify EVERY distinct food item present.

The image is {W} pixels wide and {H} pixels tall.
- x ranges from 0 (left) to {W} (right)
- y ranges from 0 (top) to {H} (bottom)

For each UNIQUE type of food, provide exactly ONE bounding box (the best one).
Make sure to also include the "card" as a distinct item.

Return ONLY a valid Python dictionary, nothing else. Format:
{{"food_name": [x1, y1, x2, y2], "another_food": [x1, y1, x2, y2]}}

Where x1,y1 = top-left corner, x2,y2 = bottom-right corner, all in pixels."""

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": f"file://{os.path.abspath(image_path)}"},
                {"type": "text", "text": prompt_text},
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
    ).to(device)

    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=512)
    
    generated_ids_trimmed = [
        out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    raw_output = processor.batch_decode(
        generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]

    print(f"VLM Output:\n{raw_output}")

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
        print(f"Error parsing VLM output: {e}")

    print(f"Detected food boxes: {food_boxes}")

    del model
    del processor
    del inputs
    cleanup_gpu()

    return food_boxes

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

def segment_foods(image_path, food_boxes):
    print("\n[Stage 2] Segmenting with SAM3...")
    device = "cuda" if torch.cuda.is_available() else "cpu"

    import sys
    SAM3_ROOT = os.environ.get("SAM3_ROOT") or os.environ.get("FOODCAL_DIR") or os.path.dirname(os.path.abspath(__file__))
    if SAM3_ROOT not in sys.path:
        sys.path.insert(0, SAM3_ROOT)

    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    import cv2
    
    print("Loading SAM3 model...")
    
    _bpe_candidates = [
        os.path.join(SAM3_ROOT, "sam3", "assets", "bpe_simple_vocab_16e6.txt.gz"),
        os.path.join(SAM3_ROOT, "assets", "bpe_simple_vocab_16e6.txt.gz"),
    ]
    bpe_path = next((p for p in _bpe_candidates if os.path.exists(p)), None)
    
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    if device == "cuda":
        torch.autocast("cuda", dtype=torch.bfloat16).__enter__()

    model = build_sam3_image_model(bpe_path=bpe_path)
    model.to(device)
    model.eval()
    
    processor = Sam3Processor(model, confidence_threshold=0.3, device=device)
    
    image = Image.open(image_path).convert("RGB")
    img_w, img_h = image.size

    fig_labeled, ax_labeled = plt.subplots(figsize=(10, 10))
    ax_labeled.imshow(image)
    ax_labeled.axis('off')

    mask_solid = np.zeros((img_h, img_w, 3), dtype=np.uint8)
    food_color_map = {}

    inference_state = processor.set_image(image)

    # 遮罩顏色池，card 固定藍色
    PALETTE = [
        [0.00, 0.00, 1.00],   # 0 藍      → card
        [1.00, 0.20, 0.20],   # 1 紅
        [1.00, 0.80, 0.00],   # 2 黃
        [0.20, 0.80, 0.20],   # 3 亮綠
        [1.00, 0.40, 0.00],   # 4 橙
        [0.60, 0.00, 0.80],   # 5 紫
        [0.00, 0.80, 0.80],   # 6 青
        [1.00, 0.40, 0.70],   # 7 粉紅
        [0.40, 0.80, 0.00],   # 8 黃綠
        [0.00, 0.60, 0.40],   # 9 深綠
        [0.80, 0.20, 0.60],   # 10 梅紫
        [0.80, 0.60, 0.00],   # 11 棕黃
    ]
    _palette_idx = 1

    food_items = list(food_boxes.keys())
    if "card" in food_items:
        food_items.remove("card")
        food_items.insert(0, "card")

    for food in food_items:
        print(f"Segmenting: {food}...")

        if food.lower() == "card":
            color = PALETTE[0]
        else:
            color = PALETTE[_palette_idx % len(PALETTE)]
            _palette_idx += 1

        processor.reset_all_prompts(inference_state)

        if food in food_boxes and food_boxes[food] and len(food_boxes[food]) == 4:
            x1, y1, x2, y2 = food_boxes[food]
            box_norm = xyxy_to_cxcywh_norm(x1, y1, x2, y2, img_w, img_h)
            state = processor.add_geometric_prompt(
                box=box_norm,
                label=True,
                state=inference_state,
            )
            print(f"  Box prompt: [{x1},{y1},{x2},{y2}] -> normalized {box_norm}")
        else:
            print(f"  No box available for {food}, skipping this item since we rely on SAM3 geometric prompt.")
            continue

        masks = state.get("masks")
        scores = state.get("scores")

        if masks is None or masks.shape[0] == 0:
            print(f"  No valid masks found for {food}")
            continue

        best_idx = scores.argmax().item()
        best_mask = masks[best_idx, 0]
        best_score = scores[best_idx].item()
        
        print(f"  Picked best mask (score: {best_score:.3f})")

        mask_2d = best_mask.cpu().numpy()
        mask_2d = (mask_2d > 0.5).astype(np.uint8)
        
        kernel = np.ones((3, 3), np.uint8)
        mask_2d = cv2.morphologyEx(mask_2d, cv2.MORPH_OPEN, kernel)
        mask_2d = cv2.morphologyEx(mask_2d, cv2.MORPH_CLOSE, kernel)

        total_pixels = mask_2d.sum()
        if total_pixels < 500:
            print(f"  Skipping {food} (too small after cleaning)")
            continue

        # Visualization
        display_color = np.concatenate([np.array(color), np.array([0.5])])
        h, w = mask_2d.shape
        mask_overlay = mask_2d.reshape(h, w, 1) * display_color.reshape(1, 1, -1)
        ax_labeled.imshow(mask_overlay)

        solid_rgb = (np.array(color[:3]) * 255).astype(np.uint8)
        food_color_map[food] = solid_rgb.tolist()
        mask_solid[mask_2d.astype(bool)] = solid_rgb

        y_indices, x_indices = np.where(mask_2d)
        if len(y_indices) > 0:
            y_center = np.mean(y_indices)
            x_center = np.mean(x_indices)
            ax_labeled.text(x_center, y_center, f"{food}", color='white',
                            fontsize=10, fontweight='bold', ha='center', va='center',
                            bbox=dict(facecolor=color, alpha=0.7, edgecolor='none'))

    base = image_path.rsplit('.', 1)[0]
    output_labeled = base + "_segmented_labeled.png"
    output_solid   = base + "_segmented_clean.png"
    color_map_path = base + "_color_map.json"

    fig_labeled.savefig(output_labeled, bbox_inches='tight', pad_inches=0, dpi=150)
    print(f"Saved labeled result  -> {output_labeled}")

    import cv2 as cv2_imwrite
    mask_bgr = cv2_imwrite.cvtColor(mask_solid, cv2_imwrite.COLOR_RGB2BGR)
    cv2_imwrite.imwrite(output_solid, mask_bgr)
    print(f"Saved solid mask      -> {output_solid}")

    with open(color_map_path, "w") as f:
        json.dump(food_color_map, f, indent=2)
    print(f"Saved color map       -> {color_map_path}")

    del model
    del processor
    if 'inference_state' in locals():
        del inference_state
    cleanup_gpu()
    plt.close('all')

def run():
    print("=== Auto Food Segmentation Pipeline ===")
    while True:
        try:
            image_path = input("\nEnter image path (default: /home/bl515-01/sam3/90.jpg): ").strip()
            if image_path.lower() in ['exit', 'quit', 'q']:
                break
            if not image_path:
                image_path = "/home/bl515-01/sam3/90.jpg"
            if not os.path.exists(image_path):
                print("Image not found!")
                continue
            food_boxes = get_food_boxes(image_path)
            if food_boxes is None:
                food_boxes = {}
            if "card" not in food_boxes:
                food_boxes["card"] = []
            segment_foods(image_path, food_boxes)
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            cleanup_gpu()

if __name__ == "__main__":
    run()
