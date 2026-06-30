import os
import sys
import torch
import numpy as np
import json
import cv2
from PIL import Image
import matplotlib.pyplot as plt

# Add DUSt3R path
DUSTER_ROOT = '/home/bl515-01/sam3/duster'
if DUSTER_ROOT not in sys.path:
    sys.path.append(DUSTER_ROOT)

from dust3r.model import AsymmetricCroCo3DStereo
from dust3r.utils.image import load_images
from dust3r.image_pairs import make_pairs
from dust3r.inference import inference
from dust3r.cloud_opt import global_aligner, GlobalAlignerMode

def get_mask_from_contour(shape, contour_list):
    mask = np.zeros(shape, dtype=np.uint8)
    for polygon in contour_list:
        pts = np.array(polygon, dtype=np.int32)
        cv2.fillPoly(mask, [pts], (1))
    return mask.astype(bool)

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    image_files = ['/home/bl515-01/sam3/1.jpg', '/home/bl515-01/sam3/2.jpg', '/home/bl515-01/sam3/3.jpg']
    contours_file = '/home/bl515-01/sam3/90_contours.json'
    
    with open(contours_file, 'r') as f:
        contours_data = json.load(f)

    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    imgs = load_images(image_files, size=image_size)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    all_pts3d = scene.get_pts3d()
    pts3d_ref = all_pts3d[2].detach().cpu().numpy()
    
    H_model, W_model = pts3d_ref.shape[:2]
    ref_img = Image.open(image_files[2])
    W_orig, H_orig = ref_img.size

    # Height Map Visualization
    # Create a blank image for height map
    height_map = np.zeros((H_model, W_model), dtype=np.float32)
    
    for food, contour_list in contours_data.items():
        if food == "card": continue
        
        food_mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        food_mask = cv2.resize(food_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        
        food_pts = pts3d_ref[food_mask]
        if len(food_pts) == 0: continue
            
        z_vals = food_pts[:, 2]
        z_base = np.percentile(z_vals, 95) 
        heights = np.maximum(0, z_base - z_vals)
        
        # Write back to height map
        height_map[food_mask] = heights

    # Normalize height map for visualization
    max_h = np.max(height_map)
    if max_h > 0:
        height_vis = (height_map / max_h * 255).astype(np.uint8)
        height_vis = cv2.applyColorMap(height_vis, cv2.COLORMAP_JET)
        # Set zero height (outside masks) to black
        height_vis[height_map == 0] = 0
    else:
        height_vis = np.zeros((H_model, W_model, 3), dtype=np.uint8)

    # Save visualization
    vis_path = "/home/bl515-01/sam3/height_map_visualization.png"
    cv2.imwrite(vis_path, height_vis)
    print(f"Height map visualization saved to {vis_path}")

if __name__ == '__main__':
    main()
