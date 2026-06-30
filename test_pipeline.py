import os
import sys

# Change default paths to sam3 local folder to work smoothly here
os.environ["SAM3_ROOT"] = "/home/bl515-01/sam3"

try:
    import integrated_food_segmentation as ifs
    ifs.SAM3_ROOT = "/home/bl515-01/sam3"
    ifs.BASE_DIR = "/home/bl515-01/sam3"
    ifs.BBOX_DIR = "/home/bl515-01/sam3/test_bboxes"
    ifs.MASK_DIR = "/home/bl515-01/sam3/test_masks"
except Exception as e:
    print(f"Import error: {e}")
    sys.exit(1)

def run():
    target_images = ["/home/bl515-01/sam3/a.jpg"]
    
    os.makedirs(ifs.BBOX_DIR, exist_ok=True)
    os.makedirs(ifs.MASK_DIR, exist_ok=True)
    
    print("Testing Stage 1...")
    ifs.detection_stage(target_images)
    
    print("Testing Stage 2...")
    ifs.segmentation_stage()
    
    print("Test Complete.")

if __name__ == "__main__":
    run()
