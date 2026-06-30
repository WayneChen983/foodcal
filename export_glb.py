import os
import sys
import torch

# Add DUSt3R path
DUSTER_ROOT = '/home/bl515-01/sam3/duster'
if DUSTER_ROOT not in sys.path:
    sys.path.append(DUSTER_ROOT)

from dust3r.model import AsymmetricCroCo3DStereo
from dust3r.demo import get_reconstructed_scene

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
    image_size = 512
    
    # Image files
    filelist = [
        '/home/bl515-01/sam3/1.jpg',
        '/home/bl515-01/sam3/2.jpg',
        '/home/bl515-01/sam3/3.jpg'
    ]
    
    # Check if files exist
    for f in filelist:
        if not os.path.exists(f):
            print(f"Error: {f} not found!")
            return

    print(f"Loading model: {model_name}...")
    model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
    
    # Parameters from demo.py defaults
    schedule = 'linear'
    niter = 300
    min_conf_thr = 3.0
    as_pointcloud = False
    mask_sky = False
    clean_depth = True
    transparent_cams = False
    cam_size = 0.05
    scenegraph_type = "complete"
    winsize = 1
    refid = 0
    silent = False
    
    outdir = '/home/bl515-01/sam3'
    
    print("Running reconstruction...")
    scene, outfile, imgs = get_reconstructed_scene(
        outdir, model, device, silent, image_size, filelist, schedule, niter, min_conf_thr,
        as_pointcloud, mask_sky, clean_depth, transparent_cams, cam_size,
        scenegraph_type, winsize, refid
    )
    
    print(f"Success! GLB exported to: {outfile}")

if __name__ == '__main__':
    main()
