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
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered)
    normal = vh[2, :]
    a, b, c = normal
    d = -np.dot(normal, centroid)
    return a, b, c, d

def get_distance_to_plane(points, plane_params):
    a, b, c, d = plane_params
    norm = np.sqrt(a**2 + b**2 + c**2)
    # Signed distance
    return (a * points[:, 0] + b * points[:, 1] + c * points[:, 2] + d) / norm

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    # 3.jpg is the reference view for contours
    image_files = ['/home/bl515-01/sam3/1.jpg', '/home/bl515-01/sam3/2.jpg', '/home/bl515-01/sam3/3.jpg']
    contours_file = '/home/bl515-01/sam3/90_contours.json'
    
    with open(contours_file, 'r') as f:
        contours_data = json.load(f)

    # Load resources
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    imgs = load_images(image_files, size=image_size)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    
    # Reconstruction
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    # Gather ALL 3D points from all 3 views
    # all_pts3d is a list of [H, W, 3] tensors
    all_pts3d_tensors = scene.get_pts3d()
    pts3d_all_views = [p.detach().cpu().numpy() for p in all_pts3d_tensors]
    
    # Reference view data (View 3 at index 2)
    pts3d_ref = pts3d_all_views[2]
    H_model, W_model = pts3d_ref.shape[:2]
    ref_img = Image.open(image_files[2])
    W_orig, H_orig = ref_img.size

    # 1. Coordinate Mapping (View 3 is our contour source)
    # ----------------------------------------------------
    grid_res = 512
    # We create a mapping: pixel(x,y) in View 3 -> 3D point(X,Y,Z) in Scene
    # Then we project all points from all views onto the View 3 Plane for binning.
    
    # 2. Scale Calibration (Card Diagonal)
    # -----------------------------------
    REAL_DIAGONAL_CM = np.sqrt(8.56**2 + 5.40**2)
    card_mask_orig = get_mask_from_contour((H_orig, W_orig), contours_data["card"])
    card_mask = cv2.resize(card_mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
    card_pts = pts3d_ref[card_mask]
    
    model_diagonal = np.max(pdist(card_pts[::max(1, len(card_pts)//500)]))
    cm_per_model_unit = REAL_DIAGONAL_CM / model_diagonal
    print(f"Calibration: 1 model unit = {cm_per_model_unit:.4f} cm")

    # 3. Reference Plane Fitting (Plate)
    # ----------------------------------
    # Use card points and boundary of foods from View 3
    plate_pts_list = [card_pts[::10]]
    for food, contour_list in contours_data.items():
        if food == "card": continue
        mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        kernel = np.ones((5,5), np.uint8)
        eroded = cv2.erode(mask_orig.astype(np.uint8), kernel, iterations=1)
        boundary_mask = cv2.resize((mask_orig - eroded), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        if np.any(boundary_mask):
            plate_pts_list.append(pts3d_ref[boundary_mask][::5])
            
    plate_pts = np.concatenate(plate_pts_list, axis=0)
    plane_params = fit_plane(plate_pts)

    # 4. Universal Thickness Integration (All Views)
    # ----------------------------------------------
    # To handle overhangs, we project ALL points from ALL views onto a grid.
    # We'll use View 3's 2D grid as the integration base.
    
    results = {}
    thickness_map = np.zeros((H_model, W_model), dtype=np.float32)

    # For each food item
    for food, contour_list in contours_data.items():
        if food == "card": continue
        
        # Get pixels in View 3 that belong to this food
        mask_orig = get_mask_from_contour((H_orig, W_orig), contour_list)
        mask_ref = cv2.resize(mask_orig.astype(np.uint8), (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        
        # We need ALL 3D points from ALL views that correspond to this food.
        # This is a bit complex. DUSt3R doesn't give us multi-view segmentation automatically.
        # But we can use geometric proximity: 
        # project all points from all views onto View 3's camera image.
        
        food_points_3d = []
        
        # Points from View 3
        food_points_3d.append(pts3d_ref[mask_ref])
        
        # Points from View 1 & 2
        # For each point in other views, project it to View 3's image and check the mask.
        cam_poses = scene.get_im_poses() # [3, 4, 4]
        K = scene.get_focals() # [3]
        
        # Simple projection: 
        # Point P in World -> Cam 3 Frame -> Image 3
        inv_pose_ref = torch.inverse(cam_poses[2]).detach().cpu().numpy()
        focal_ref = K[2].detach().cpu().item()
        
        for v_idx in [0, 1]:
            v_pts = pts3d_all_views[v_idx].reshape(-1, 3)
            # Transform to Ref Cam Frame
            pts_hom = np.hstack([v_pts, np.ones((v_pts.shape[0], 1))])
            pts_cam = pts_hom @ inv_pose_ref.T
            
            # Project
            z = pts_cam[:, 2]
            valid = z > 0.01
            px = focal_ref * pts_cam[valid, 0] / z[valid] + W_model / 2
            py = focal_ref * pts_cam[valid, 1] / z[valid] + H_model / 2
            
            # Filter by Image 3 bounds and Food Mask
            ix = np.round(px).astype(int)
            iy = np.round(py).astype(int)
            
            in_bounds = (ix >= 0) & (ix < W_model) & (iy >= 0) & (iy < H_model)
            if not np.any(in_bounds): continue
            
            masked = mask_ref[iy[in_bounds], ix[in_bounds]]
            food_points_3d.append(v_pts[valid][in_bounds][masked])
            
        all_food_pts = np.concatenate(food_points_3d, axis=0)
        
        # Binning: Divide the food region into cells
        # We'll use the View 3 pixels as bins.
        # For each point, find which (ix, iy) it falls into in View 3.
        pts_hom = np.hstack([all_food_pts, np.ones((all_food_pts.shape[0], 1))])
        pts_cam = pts_hom @ inv_pose_ref.T
        z = pts_cam[:, 2]
        ix = np.round(focal_ref * pts_cam[:, 0] / z + W_model / 2).astype(int)
        iy = np.round(focal_ref * pts_cam[:, 1] / z + H_model / 2).astype(int)
        
        # Heights relative to plate plane (use signed distance to handle orientation)
        h_vals = get_distance_to_plane(all_food_pts, plane_params)
        h_vals = np.abs(h_vals) # Perpendicular distance
        
        # For each bin (ix, iy), find Min/Max Height
        # We use a trick with np.maximum.at and np.minimum.at
        valid = (ix >= 0) & (ix < W_model) & (iy >= 0) & (iy < H_model) & mask_ref[np.clip(iy, 0, H_model-1), np.clip(ix, 0, W_model-1)]
        ix, iy, h_vals = ix[valid], iy[valid], h_vals[valid]
        
        indices = iy * W_model + ix
        max_h = np.full(H_model * W_model, -np.inf)
        min_h = np.full(H_model * W_model, np.inf)
        
        np.maximum.at(max_h, indices, h_vals)
        np.minimum.at(min_h, indices, h_vals)
        
        # Thickness = Max - Min
        mask_flat = mask_ref.flatten()
        thickness = np.zeros_like(max_h)
        has_points = (max_h != -np.inf) & mask_flat
        
        # Logic: 
        # If we have Z_min significantly above plate, it's an overhang.
        # Thickness = Z_max - Z_min.
        # If Z_min is basically the plate (or we only have one surface), Z_min = 0.
        # Let's say if Z_min > 0.5cm (in model units), it's a gap.
        # For a universal method, we strictly use Z_max - Z_min for captured parts.
        # BUT if we only saw the top surface, Z_min will equal Z_max in that bin.
        # We need to know if the bin represents a search for the bottom.
        
        # Improved Logic: 
        # Volume = Integral of (Z_max - Z_min)
        # where Z_min is either the captured bottom OR the plate.
        # If Z_min (captured) > 0, and we are sure there is air underneath (from side view), use it.
        # If Z_min (captured) is close to plate, use plate.
        
        # Universal approach: 
        # For each bin, Thickness = Z_max - (Z_min if Z_min captured else 0).
        # Actually, Z_min should be the lowest point of the OBJECT.
        # If the object touches the plate, Z_min ~ 0.
        # If the object is a floating canopy, Z_min > 0.
        # So sum(Z_max - Z_min) is the VOLUME OF THE MATERIAL.
        
        # One catch: if a bin only has 1 point, Z_max - Z_min = 0.
        # We need to fill holes where we only saw the top.
        # Valid Thickness: 
        # If (Max - Min) > threshold, use it.
        # Else if only saw Top, use (Max - 0)? No, that's what caused the error.
        # BUT if side views DID NOT see anything underneath, it might be solid... 
        # OR it might just be obscured. 
        # Strict Multi-view: only subtract what we SEE is empty.
        
        # Let's use: Thickness = Max - Min. 
        # If Max == Min (only one surface), we assume it's Solid down to plate: Thickness = Max.
        # If Max > Min, it's a shell: Thickness = Max - Min.
        
        valid_bins = (max_h != -np.inf)
        diff = max_h[valid_bins] - min_h[valid_bins]
        
        # Threshold to distinguish "shell" vs "single point"
        is_shell = diff > (0.005 / cm_per_model_unit) # more than 5mm thick gap
        
        final_h = np.zeros_like(max_h)
        # For shells, thickness is the gap. For solids, thickness is height.
        # Wait, if it's a shell, the "material" is the thickness of the canopy?
        # No, the user wants the volume of the food. 
        # If broccoli canopy is 2cm thick and 5cm above plate:
        # Integrated Height was 7cm (Wrong).
        # New Thickness = 2cm (Right).
        
        final_h[valid_bins] = np.where(is_shell, diff, max_h[valid_bins])
        
        # Sum volume
        dx = np.median(np.linalg.norm(pts3d_ref[:, 1:] - pts3d_ref[:, :-1], axis=2))
        dy = np.median(np.linalg.norm(pts3d_ref[1:, :] - pts3d_ref[:-1, :], axis=2))
        vol_cm3 = np.sum(final_h[mask_flat]) * dx * dy * (cm_per_model_unit ** 3)
        
        results[food] = vol_cm3
        thickness_map.flat[valid_bins] = final_h[valid_bins]
        print(f"Food: {food:<12} Volume: {vol_cm3:.2f} cm^3")

    # Save
    with open("/home/bl515-01/sam3/food_volumes_universal.json", 'w') as f:
        json.dump(results, f, indent=2)
    
    max_vt = np.max(thickness_map)
    if max_vt > 0:
        vis = (thickness_map / max_vt * 255).astype(np.uint8)
        vis = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
        vis[thickness_map == 0] = 0
    else:
        vis = np.zeros((H_model, W_model, 3), dtype=np.uint8)
    cv2.imwrite("/home/bl515-01/sam3/thickness_map_universal.png", vis)
    print("\nUniversal results and thickness map saved.")

if __name__ == '__main__':
    main()
