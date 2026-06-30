import os
import sys
import torch
import numpy as np
import json
import cv2
from PIL import Image
from scipy.spatial.distance import pdist

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

def fit_plane(points):
    """
    Fit a plane ax + by + cz + d = 0 to a set of 3D points.
    Returns (a, b, c, d)
    """
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    # SVD
    _, _, vh = np.linalg.svd(centered)
    normal = vh[2, :] # Last row is the normal vector
    a, b, c = normal
    d = -np.dot(normal, centroid)
    return a, b, c, d

def point_to_plane_dist(points, plane_params):
    """
    Calculate perpendicular distance from points to plane.
    """
    a, b, c, d = plane_params
    norm = np.sqrt(a**2 + b**2 + c**2)
    return np.abs(a * points[:, 0] + b * points[:, 1] + c * points[:, 2] + d) / norm

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    image_files = ['/home/bl515-01/sam3/1.jpg', '/home/bl515-01/sam3/2.jpg', '/home/bl515-01/sam3/3.jpg']
    contours_file = '/home/bl515-01/sam3/90_contours.json'
    
    with open(contours_file, 'r') as f:
        contours_data = json.load(f)

    print(f"Loading DUSt3R model: {model_name}...")
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    
    print("Running DUSt3R inference...")
    imgs = load_images(image_files, size=image_size)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    
    print("Aligning 3D scene...")
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    all_pts3d = scene.get_pts3d()
    pts3d_ref = all_pts3d[2].detach().cpu().numpy() # (H_model, W_model, 3)
    H_model, W_model = pts3d_ref.shape[:2]

    ref_img = Image.open(image_files[2])
    W_orig, H_orig = ref_img.size

    # 1. Scale Calibration (Card Diagonal)
    # -----------------------------------
    REAL_DIAGONAL_CM = np.sqrt(8.56**2 + 5.40**2) # ~10.12 cm
    card_mask_orig = get_mask_from_contour((H_orig, W_orig), contours_data["card"])
    card_mask = cv2.resize(card_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
    card_pts = pts3d_ref[card_mask]
    
    # Subsample for speed
    sub_pts = card_pts[::max(1, len(card_pts) // 500)]
    model_diagonal = np.max(pdist(sub_pts))
    cm_per_model_unit = REAL_DIAGONAL_CM / model_diagonal
    print(f"Calibration: 1 model unit = {cm_per_model_unit:.4f} cm")

    # 2. Identify Plate Points for Plane Fitting
    # ------------------------------------------
    # Points on the card are definitely on the plate
    plate_pts_list = [card_pts[::10]] # Subsample card points
    
    # Also add boundary points from food masks (assuming they touch the plate)
    for food, contour_list in contours_data.items():
        if food == "card": continue
        
        # Get boundary by eroding mask
        mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        kernel = np.ones((5,5), np.uint8)
        eroded = cv2.erode(mask_orig.astype(np.uint8), kernel, iterations=1)
        boundary_mask_orig = (mask_orig.astype(np.uint8) - eroded).astype(bool)
        
        boundary_mask = cv2.resize(boundary_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        b_pts = pts3d_ref[boundary_mask]
        if len(b_pts) > 0:
            plate_pts_list.append(b_pts[::5])

    all_plate_pts = np.concatenate(plate_pts_list, axis=0)
    print(f"Fitting reference plane using {len(all_plate_pts)} points...")
    plane_params = fit_plane(all_plate_pts)

    # 3. Calculate True Height and Volume
    # -----------------------------------
    # Average model pixel area
    dx = np.median(np.linalg.norm(pts3d_ref[:, 1:] - pts3d_ref[:, :-1], axis=2))
    dy = np.median(np.linalg.norm(pts3d_ref[1:, :] - pts3d_ref[:-1, :], axis=2))
    avg_model_pixel_area = dx * dy

    results = {}
    height_map = np.zeros((H_model, W_model), dtype=np.float32)

    for food, contour_list in contours_data.items():
        if food == "card": continue
        
        food_mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        food_mask = cv2.resize(food_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        
        food_pts = pts3d_ref[food_mask]
        if len(food_pts) == 0: continue
            
        # Calculate perpendicular heights to the fitted plane
        heights = point_to_plane_dist(food_pts, plane_params)
        
        # We need to ensure we only count points *above* the plane.
        # Check signed distance if needed, but for simplicity we assume all are above.
        # To be safe, compare with the median plate Z or similar.
        
        vol_model = np.sum(heights) * avg_model_pixel_area
        vol_cm3 = vol_model * (cm_per_model_unit ** 3)
        
        results[food] = vol_cm3
        height_map[food_mask] = heights
        print(f"Food: {food:<12} Volume: {vol_cm3:.2f} cm^3")

    # 4. Save Results and Visualization
    # ---------------------------------
    with open("/home/bl515-01/sam3/food_volumes_refined.json", 'w') as f:
        json.dump(results, f, indent=2)

    max_h = np.max(height_map)
    if max_h > 0:
        height_vis = (height_map / max_h * 255).astype(np.uint8)
        height_vis = cv2.applyColorMap(height_vis, cv2.COLORMAP_JET)
        height_vis[height_map == 0] = 0
    else:
        height_vis = np.zeros((H_model, W_model, 3), dtype=np.uint8)

    cv2.imwrite("/home/bl515-01/sam3/height_map_refined_planar.png", height_vis)
    print("\nRefined volumes and height map saved.")

if __name__ == '__main__':
    main()
