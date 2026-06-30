import cv2
import numpy as np
import json
import os

def main():
    image_base = "/home/bl515-01/sam3/a"
    mask_path = image_base + "_segmented_clean.png"
    color_map_path = image_base + "_color_map.json"
    output_path = image_base + "_contours.json"

    if not os.path.exists(mask_path) or not os.path.exists(color_map_path):
        print("Required files missing")
        return

    mask_bgr = cv2.imread(mask_path)
    with open(color_map_path, 'r') as f:
        color_map = json.load(f)

    contours_results = {}

    for label, color in color_map.items():
        # Match color (BGR comparison)
        # Note: auto_pipeline saves as RGB colors in JSON, but imread gets BGR
        target_bgr = np.array([color[2], color[1], color[0]], dtype=np.uint8)
        
        # Create mask for this color
        binary_mask = cv2.inRange(mask_bgr, target_bgr, target_bgr)
        
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        valid_contours = []
        for cnt in contours:
            if cv2.contourArea(cnt) > 100:
                # Convert to (N, 2) list of [x, y]
                valid_contours.append(cnt.squeeze().tolist())
        
        contours_results[label] = valid_contours

    with open(output_path, 'w') as f:
        json.dump(contours_results, f, indent=2)
    print(f"Extraction complete: {output_path}")

if __name__ == "__main__":
    main()
