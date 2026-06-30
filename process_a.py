import os
import sys
from auto_pipeline import get_food_boxes, segment_foods

def main():
    image_path = "/home/bl515-01/sam3/a.jpg"
    if not os.path.exists(image_path):
        print(f"Error: {image_path} not found")
        return

    # Stage 1: VLM → {food: [x1,y1,x2,y2]}
    food_boxes = get_food_boxes(image_path) or {}

    # Ensure card exists
    if not food_boxes:
        print("No food boxes detected, adding card fallback...")
        food_boxes = {"card": [645, 855, 930, 1040]}
    elif "card" not in food_boxes:
        print("Adding card box to detections...")
        food_boxes["card"] = [645, 855, 930, 1040]
        
    print(f"Final boxes for segmentation: {food_boxes}")

    # Stage 2: SAM3 Segmentation
    segment_foods(image_path, food_boxes)
    print("\nSegmentation of a.jpg complete.")

if __name__ == "__main__":
    main()
