import os
import sys
import json
import torch
import gc
import numpy as np
import cv2
from PIL import Image
from scipy.spatial.distance import pdist

# Paths and Config
BASE_DIR = "/home/bl515-01/sam3"
DUSTER_ROOT = os.path.join(BASE_DIR, "duster")
if DUSTER_ROOT not in sys.path:
    sys.path.append(DUSTER_ROOT)

from auto_pipeline import get_food_boxes, segment_foods, cleanup_gpu
from dust3r.model import AsymmetricCroCo3DStereo
from dust3r.utils.image import load_images
from dust3r.image_pairs import make_pairs
from dust3r.inference import inference
from dust3r.cloud_opt import global_aligner, GlobalAlignerMode
from calculate_nutrition_static import calculate_nutrition, load_db

def extract_contours(image_base):
    mask_path = f"{image_base}_segmented_clean.png"
    color_map_path = f"{image_base}_color_map.json"
    
    if not os.path.exists(mask_path) or not os.path.exists(color_map_path):
        print(f"Error: Mask or Color Map missing for {image_base}")
        return None

    mask_bgr = cv2.imread(mask_path)
    with open(color_map_path, 'r') as f:
        color_map = json.load(f)

    contours_results = {}
    for label, color in color_map.items():
        # Match color (auto_pipeline saves as RGB, imread is BGR)
        target_bgr = np.array([color[2], color[1], color[0]], dtype=np.uint8)
        binary_mask = cv2.inRange(mask_bgr, target_bgr, target_bgr)
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        valid_contours = []
        for cnt in contours:
            if cv2.contourArea(cnt) > 100:
                valid_contours.append(cnt.squeeze().tolist())
        contours_results[label] = valid_contours
        
    return contours_results

def get_mask_from_contour(shape, contour_list):
    mask = np.zeros(shape, dtype=np.uint8)
    for polygon in contour_list:
        pts = np.array(polygon, dtype=np.int32)
        if pts.ndim == 1: continue
        cv2.fillPoly(mask, [pts], (1))
    return mask.astype(bool)

def fit_plane(points):
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered)
    normal = vh[2, :]
    d = -np.dot(normal, centroid)
    return normal[0], normal[1], normal[2], d

def get_distance_to_plane(points, plane_params):
    a, b, c, d = plane_params
    norm = np.sqrt(a**2 + b**2 + c**2)
    return (a * points[:, 0] + b * points[:, 1] + c * points[:, 2] + d) / norm

def run_master_pipeline(image_files, ref_idx=2):
    """
    image_files: list of image paths (e.g., ['1.jpg', '2.jpg', '3.jpg'])
    ref_idx: index of the reference (top-down) image for segmentation
    """
    ref_image = image_files[ref_idx]
    image_base = ref_image.rsplit('.', 1)[0]
    
    # --- PHASE 1: Segmentation ---
    print("\n>>> PHASE 1: VLM + SAM Segmentation")
    food_boxes = get_food_boxes(ref_image)
    if not food_boxes:
        print("Warning: No detections, adding manual card box fallback.")
        food_boxes = {"card": []}
    if "card" not in food_boxes:
        food_boxes["card"] = []
        
    segment_foods(ref_image, food_boxes)
    contours_data = extract_contours(image_base)
    
    if not contours_data:
        print("Error: Failed to obtain contours.")
        return

    # --- PHASE 2: 3D Volume Calculation ---
    print("\n>>> PHASE 2: 3D Reconstruction & Volume Analysis")
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    
    # Load DUSt3R
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    imgs = load_images(image_files, size=512)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    pts3d_all_views = [p.detach().cpu().numpy() for p in scene.get_pts3d()]
    pts3d_ref = pts3d_all_views[ref_idx]
    H_model, W_model = pts3d_ref.shape[:2]
    
    orig_img = Image.open(ref_image)
    W_orig, H_orig = orig_img.size

    # Scale Calibration
    REAL_DIAGONAL_CM = np.sqrt(8.56**2 + 5.40**2)
    card_mask = cv2.resize(get_mask_from_contour((H_orig, W_orig), contours_data.get("card", [])).astype(np.uint8), 
                          (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
    card_pts = pts3d_ref[card_mask]
    if len(card_pts) < 10:
        print("Error: Card pts missing in 3D scene.")
        return
    model_diagonal = np.max(pdist(card_pts[::max(1, len(card_pts)//500)]))
    cm_per_model_unit = REAL_DIAGONAL_CM / model_diagonal
    print(f"Calibration: 1 unit = {cm_per_model_unit:.4f} cm")

    # Plane Fitting
    plate_pts_list = [card_pts[::10]]
    for food, cnt_list in contours_data.items():
        if food == "card": continue
        m_orig = get_mask_from_contour((H_orig, W_orig), cnt_list)
        boundary = m_orig - cv2.erode(m_orig.astype(np.uint8), np.ones((5,5),np.uint8))
        b_mask = cv2.resize(boundary, (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        if np.any(b_mask):
            plate_pts_list.append(pts3d_ref[b_mask][::5])
    plate_pts = np.concatenate(plate_pts_list, axis=0)
    plane_params = fit_plane(plate_pts)

    # Thickness Integration
    inv_pose_ref = torch.inverse(scene.get_im_poses()[ref_idx]).detach().cpu().numpy()
    focal_ref = scene.get_focals()[ref_idx].detach().cpu().item()
    
    final_volumes = {}
    thickness_map = np.zeros((H_model, W_model), dtype=np.float32)

    for food, cnt_list in contours_data.items():
        if food == "card": continue
        m_ref = cv2.resize(get_mask_from_contour((H_orig, W_orig), cnt_list).astype(np.uint8), 
                          (W_model, H_model), interpolation=cv2.INTER_NEAREST).astype(bool)
        
        food_pts_3d = [pts3d_ref[m_ref]]
        for v_idx in range(len(image_files)):
            if v_idx == ref_idx: continue
            v_pts = pts3d_all_views[v_idx].reshape(-1, 3)
            pts_hom = np.hstack([v_pts, np.ones((v_pts.shape[0], 1))])
            pts_cam = pts_hom @ inv_pose_ref.T
            z = pts_cam[:, 2]
            valid = z > 0.01
            px = focal_ref * pts_cam[valid, 0] / z[valid] + W_model / 2
            py = focal_ref * pts_cam[valid, 1] / z[valid] + H_model / 2
            ix, iy = np.round(px).astype(int), np.round(py).astype(int)
            ib = (ix>=0) & (ix<W_model) & (iy>=0) & (iy<H_model)
            masked = m_ref[iy[ib], ix[ib]]
            food_pts_3d.append(v_pts[valid][ib][masked])
            
        all_food_pts = np.concatenate(food_pts_3d, axis=0)
        pts_hom = np.hstack([all_food_pts, np.ones((all_food_pts.shape[0], 1))])
        pts_cam = pts_hom @ inv_pose_ref.T
        z = pts_cam[:, 2]
        ix = np.round(focal_ref * pts_cam[:, 0] / z + W_model / 2).astype(int)
        iy = np.round(focal_ref * pts_cam[:, 1] / z + H_model / 2).astype(int)
        h_vals = np.abs(get_distance_to_plane(all_food_pts, plane_params))
        
        valid = (ix>=0) & (ix<W_model) & (iy>=0) & (iy<H_model) & m_ref[np.clip(iy,0,H_model-1), np.clip(ix,0,W_model-1)]
        ix, iy, h_vals = ix[valid], iy[valid], h_vals[valid]
        indices = iy * W_model + ix
        max_h, min_h = np.full(H_model*W_model, -np.inf), np.full(H_model*W_model, np.inf)
        np.maximum.at(max_h, indices, h_vals)
        np.minimum.at(min_h, indices, h_vals)
        
        valid_bins = (max_h != -np.inf)
        diff = max_h[valid_bins] - min_h[valid_bins]
        # Multi-view thickness logic: Z_max - Z_min for gaps, Z_max for solids
        f_h = np.zeros_like(max_h)
        f_h[valid_bins] = np.where(diff > (0.005 / cm_per_model_unit), diff, max_h[valid_bins])
        
        dx = np.median(np.linalg.norm(pts3d_ref[:, 1:] - pts3d_ref[:, :-1], axis=2))
        dy = np.median(np.linalg.norm(pts3d_ref[1:, :] - pts3d_ref[:-1, :], axis=2))
        vol = np.sum(f_h[m_ref.flatten()]) * dx * dy * (cm_per_model_unit ** 3)
        final_volumes[food] = vol
        thickness_map.flat[valid_bins] = f_h[valid_bins]
        print(f"  Result: {food:<12} | Volume: {vol:.2f} cm3")

    # Cleanup DUSt3R
    del model
    cleanup_gpu()

    # --- PHASE 3: Nutrition Calculation ---
    print("\n>>> PHASE 3: Nutrition Mapping")
    db = load_db(os.path.join(BASE_DIR, "food_nutrition_db.json"))
    report = calculate_nutrition(final_volumes, db)
    
    report_path = os.path.join(BASE_DIR, f"master_report_123.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print("\n" + "="*60)
    print("           FINAL INTEGRATED NUTRITION REPORT")
    print("="*60)
    print(f"{'Food':<15} | {'Vol(cm3)':>8} | {'Kcal':>6} | {'P(g)':>5} | {'F(g)':>5} | {'C(g)':>5}")
    print("-" * 60)
    for it in report["items"]:
        if it["matched_as"] == "UNKNOWN":
            name = it["original_label"][:15]
            print(f"{name:<15} | {it.get('volume_cm3', 0):>8.2f} | {'N/A':>6} | {'-':>5} | {'-':>5} | {'-':>5}")
        else:
            name = it["matched_as"].split(" ")[0]
            print(f"{name:<15} | {it.get('volume_cm3', 0):>8.2f} | {it.get('kcal', 0):>6.1f} | {it.get('protein_g', 0):>5.1f} | {it.get('fat_g', 0):>5.1f} | {it.get('carbs_g', 0):>5.1f}")
    print("-" * 60)
    t = report["totals"]
    print(f"{'TOTAL':<15} | {'-':>8} | {t['calories_kcal']:>6} | {t['protein_g']:>5} | {t['fat_g']:>5} | {t['carbohydrates_g']:>5}")
    print("="*60)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) >= 4:
        img_names = sys.argv[1:4]
    else:
        img_names = ['1.jpg', '2.jpg', '3.jpg']
        
    if len(sys.argv) >= 5:
        ref_idx = int(sys.argv[4])
    else:
        ref_idx = 2  # default fallback if 1.jpg, 2.jpg, 3.jpg is used
        
    imgs = [os.path.join(BASE_DIR, f) for f in img_names]
    run_master_pipeline(imgs, ref_idx=ref_idx)
