import sys
from integrated_food_segmentation import detection_stage, segmentation_stage

if __name__ == "__main__":
    if len(sys.argv) > 1:
        images = sys.argv[1:]
    else:
        images = ["/home/bl515-01/sam3/a.jpg"]
    
    print("Testing Pipeline on", images)
    
    # 1. Detection
    detection_stage(images)
    
    # 2. Segmentation
    segmentation_stage()
    
    print("Pipeline Test Finished!")
