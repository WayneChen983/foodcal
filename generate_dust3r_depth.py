import os
import sys
import torch
import numpy as np
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
from dust3r.utils.device import to_numpy

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    image_files = ['/home/bl515-01/sam3/1.jpg', '/home/bl515-01/sam3/2.jpg', '/home/bl515-01/sam3/3.jpg']
    
    print(f"Loading DUSt3R model: {model_name}...")
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    
    print("Running DUSt3R inference...")
    imgs = load_images(image_files, size=image_size)
    pairs = make_pairs(imgs, scene_graph='complete', prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1)
    
    print("Aligning 3D scene...")
    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init='mst', niter=300, schedule='linear', lr=0.01)
    
    print("Extracting depth maps...")
    depths = to_numpy(scene.get_depthmaps())
    
    # Save depth maps for each view
    for i, depth in enumerate(depths):
        # Normalize depth for visualization (0-255)
        # Low value (near) -> Bright, High value (far) -> Dark
        d_min, d_max = depth.min(), depth.max()
        depth_norm = (depth - d_min) / (d_max - d_min)
        depth_vis = (255 * (1 - depth_norm)).astype(np.uint8) # Invert so closer is brighter
        
        # Apply colormap
        depth_color = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)
        
        output_path = f"/home/bl515-01/sam3/dust3r_depth_view_{i+1}.png"
        cv2.imwrite(output_path, depth_color)
        print(f"Saved depth map for View {i+1} to {output_path}")

if __name__ == '__main__':
    main()
