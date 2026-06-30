import os
import sys
import torch
import numpy as np
import json
import cv2
from PIL import Image

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
    """
    Convert a list of contours (polygons) into a binary mask.
    contour_list: [[[x,y], [x,y], ...], ...]
    """
    mask = np.zeros(shape, dtype=np.uint8)
    for polygon in contour_list:
        pts = np.array(polygon, dtype=np.int32)
        cv2.fillPoly(mask, [pts], (1))
    return mask.astype(bool)

def calculate_3d_area(pts3d, mask):
    """
    Approximate the surface area of a 3D point cloud region.
    pts3d: (H, W, 3)
    mask: (H, W)
    """
    # Simply count points and multiply by average pixel area if it's relatively flat
    # For more accuracy, we could triangulate, but card is flat.
    return np.sum(mask) 

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    # Files
    image_files = [
        '/home/bl515-01/sam3/1.jpg',
        '/home/bl515-01/sam3/2.jpg',
        '/home/bl515-01/sam3/3.jpg'
    ]
    contours_file = '/home/bl515-01/sam3/90_contours.json'
    
    if not os.path.exists(contours_file):
        print(f"Error: {contours_file} not found!")
        return

    with open(contours_file, 'r') as f:
        contours_data = json.load(f)

    print(f"Loading DUSt3R model: {model_name}...")
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    
    print("Running DUSt3R inference...")
    # 這裡我們需要精確的原始影像尺寸來對應像素
    imgs = load_images(image_files, size=image_size)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    
    # Global Alignment
    print("Aligning 3D scene...")
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    # Get 3D points for the 3rd view (index 2)
    # pts3d has shape (3, H, W, 3) 
    # but scene.get_pts3d() returns a list of tensors of shape (H, W, 3)
    all_pts3d = scene.get_pts3d()
    pts3d_ref = all_pts3d[2].detach().cpu().numpy() # (H_model, W_model, 3)
    
    H_model, W_model = pts3d_ref.shape[:2]
    print(f"Model resolution: {W_model}x{H_model}")

    # Load the original image to get its dimensions for coordinate mapping
    ref_img = Image.open(image_files[2])
    W_orig, H_orig = ref_img.size
    print(f"Original image resolution: {W_orig}x{H_orig}")

    # 1. Scale Calibration using the Card
    # ----------------------------------
    CARD_REAL_AREA_CM2 = 46.2 # 8.56 * 5.40
    
    if "card" not in contours_data:
        print("Error: 'card' not found in contours for calibration!")
        return
        
    card_mask_orig = get_mask_from_contour((H_orig, W_orig), contours_data["card"])
    # Resize mask to model resolution
    card_mask = cv2.resize(card_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
    
    card_pts = pts3d_ref[card_mask]
    if len(card_pts) == 0:
        print("Error: No 3D points found for the card mask!")
        return

    # Using the average distance between points as pixel area in model units
    # Or just calculate the total model area 
    # Let's estimate the model scale by measuring the card's long edge
    # Find the two points in card_pts that are furthest apart (the diagonal)
    from scipy.spatial.distance import pdist, squareform
    
    # For large point clouds, pdist is slow. Let's use a simpler bounding box approach in 2D
    # since the card is roughly flat.
    # Model scale = Real World cm / Model unit
    # But height integration needs (Scale_X * Scale_Y * Scale_Z)
    
    # Find the diagonal distance in model units
    # Diagonal of 8.56 x 5.40 is ~10.12 cm
    REAL_DIAGONAL_CM = np.sqrt(8.56**2 + 5.40**2)
    
    # Subsample to speed up diagonal calculation
    step = max(1, len(card_pts) // 500)
    sub_pts = card_pts[::step]
    dists = pdist(sub_pts)
    model_diagonal = np.max(dists)
    
    cm_per_model_unit = REAL_DIAGONAL_CM / model_diagonal
    print(f"Calibration: 1 model unit = {cm_per_model_unit:.4f} cm")
    
    # 2. Height Integration for Food
    # ------------------------------
    results = {}
    
    # Calculate the area of a single model "pixel" in cm^2
    # Area_cm2 = (Height_unit * cm_per_unit) * (Width_unit * cm_per_unit)
    # Since DUSt3R points are a grid, we need the 3D spacing
    # Better: cm_per_model_unit applies to all dimensions.
    
    for food, contour_list in contours_data.items():
        if food == "card": continue
        
        food_mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        food_mask = cv2.resize(food_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        
        food_pts = pts3d_ref[food_mask]
        if len(food_pts) == 0:
            print(f"Skipping {food}: no 3D points.")
            continue
            
        # Z-axis in DUSt3R is usually depth. 
        # Large Z = far (plate), Small Z = near (food top)
        # We need a reference plate height. 
        # We'll take the 95th percentile of Z in the food region as the plate floor.
        z_vals = food_pts[:, 2]
        z_base = np.percentile(z_vals, 95) 
        
        # Height = z_base - z_i
        heights = np.maximum(0, z_base - z_vals)
        
        # Volume = sum(height_i * Area_per_pixel)
        # We need to estimate the 2D projected area per pixel in the model.
        # Looking at card_pts to find average spacing
        # dA = (Total card model area / card pixel count)
        # For simplicity, we can use the average 3D cross-product area 
        # but let's just use the diagonal-based scale.
        
        # Area of one image pixel in cm^2 (at the food's distance)
        # This is tricky because of perspective.
        # But DUSt3R points are 3D. Volume = sum(h * dx * dy)
        # In a grid: dx ~ (pts[x+1] - pts[x])
        # Let's use the local differentials for better accuracy.
        
        # Method: sum(h_i) * (ModelScale^3) / count? No.
        # Volume = sum( (z_base - z_i) * dx_i * dy_i )
        # dx_i * dy_i * Scale^2 is the area in cm^2.
        
        # Simplified: Use the card-calibrated scale for all 3 dimensions
        # Volume_unit3 = sum(z_base - z_i) * (1.0 / count_factor?) 
        # Wait, the pts3d_ref is a grid. Each point represents 1/ (H*W) of the view.
        # Let's find the average spacing between adjacent points in the model.
        
        # Average distance between horizontal neighbors
        dx = np.median(np.linalg.norm(pts3d_ref[:, 1:] - pts3d_ref[:, :-1], axis=2))
        # Average distance between vertical neighbors
        dy = np.median(np.linalg.norm(pts3d_ref[1:, :] - pts3d_ref[:-1, :], axis=2))
        
        avg_model_pixel_area = dx * dy
        
        vol_model = np.sum(heights) * avg_model_pixel_area
        vol_cm3 = vol_model * (cm_per_model_unit ** 3)
        
        results[food] = vol_cm3
        print(f"Food: {food:<12} Volume: {vol_cm3:.2f} cm^3")

    # Output results
    output_file = "/home/bl515-01/sam3/food_volumes.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nFinal volumes saved to {output_file}")

if __name__ == '__main__':
    main()
