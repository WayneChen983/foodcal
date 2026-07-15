#!/usr/bin/env bash
# 修复 RunPod 上已装坏的环境（Blackwell GPU + numpy + setuptools）
#
# 根因：
#   1. RTX PRO 4500 (Blackwell sm_120) 需要 PyTorch + CUDA 12.8，不能用 cu126
#   2. numpy 用 --no-deps 安装会导致 metadata 损坏 (found=None)
#   3. setuptools 83 移除 pkg_resources（sam3 已 patch，但仍建议 pin setuptools）
#
# 用法：
#   export HF_TOKEN="hf_xxxx"   # 可选，仅首次需 HF 登录时
#   bash scripts/runpod_fix_env.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOODCAL_DIR="${FOODCAL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONDA_ENV="${CONDA_ENV:-foodcal}"
MINICONDA="${MINICONDA_INSTALL_DIR:-/workspace/miniconda3}"

echo "=============================================="
echo " foodcal RunPod environment fix"
echo "=============================================="

# shellcheck disable=SC1091
source "$MINICONDA/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"
cd "$FOODCAL_DIR"

if [[ -d "$FOODCAL_DIR/.git" ]]; then
  echo "[0] git pull latest fixes..."
  git pull || true
fi

GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
echo "[info] GPU: $GPU_NAME"

echo ""
echo "[1/5] PyTorch with CUDA 12.8 (Blackwell sm_120)..."
pip install --upgrade pip wheel
# 必须先卸掉 cu126，否则 pip 会认为 torch 已安装而跳过
pip uninstall -y torch torchvision torchaudio 2>/dev/null || true
pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

python - <<'PY'
import torch
print(f"  torch {torch.__version__}, cuda={torch.cuda.is_available()}")
if "+cu126" in torch.__version__:
    raise SystemExit("ERROR: still cu126 — Blackwell needs cu128. Re-run fix.")
if not torch.cuda.is_available():
    raise SystemExit("CUDA not available")
cap = torch.cuda.get_device_capability(0)
name = torch.cuda.get_device_name(0)
x = torch.zeros(1, device="cuda")
print(f"  GPU {name} sm_{cap[0]}{cap[1]} — cuda tensor OK")
PY

echo ""
echo "[2/5] numpy 1.26.4 (SAM3 requires numpy<2)..."
# 彻底清除损坏的 numpy（可 import 但 metadata=None 会导致 transformers 失败）
python - <<'PY'
import site, shutil, pathlib, glob
roots = list(site.getsitepackages())
try:
    roots.append(site.getusersitepackages())
except Exception:
    pass
for sp in roots:
    sp = pathlib.Path(sp)
    if not sp.is_dir():
        continue
    for p in sp.glob("numpy*"):
        print(f"  removing {p}")
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
PY
pip uninstall -y numpy 2>/dev/null || true
pip install --no-cache-dir "numpy==1.26.4"
python - <<'PY'
import numpy as np
from importlib.metadata import version
print(f"  numpy {np.__version__}")
print(f"  metadata {version('numpy')}")
assert np.__version__.startswith("1.26")
assert version("numpy").startswith("1.26")
PY

echo ""
echo "[3/5] setuptools..."
pip install --force-reinstall "setuptools==75.8.0"

echo ""
echo "[4/5] Reinstall foodcal (sam3) + core deps..."
pip install -e "$FOODCAL_DIR" --no-deps
pip install -r "$FOODCAL_DIR/duster/requirements.txt" 2>/dev/null || true
pip install transformers accelerate opencv-python-headless scipy matplotlib pillow tqdm qwen-vl-utils einops
pip install --force-reinstall "numpy==1.26.4"

if [[ -n "${HF_TOKEN:-}" ]]; then
  hf auth login --token "$HF_TOKEN" 2>/dev/null \
    || huggingface-cli login --token "$HF_TOKEN" || true
fi

echo ""
echo "[5/5] Smoke test..."
python - <<'PY'
import importlib
import numpy as np
import torch

assert tuple(int(x) for x in np.__version__.split(".")[:2]) < (2, 0)
mods = ["torch", "sam3", "transformers", "cv2", "scipy"]
for m in mods:
    importlib.import_module(m)
    print(f"  OK  {m}")
print("\n==============================================")
print(" Environment FIXED — ready for benchmark")
print("==============================================")
PY

ENV_FILE="${FOODCAL_ENV_FILE:-/workspace/.foodcal_env}"
cat > "$ENV_FILE" <<EOF
export FOODCAL_DIR="$FOODCAL_DIR"
export SAM3_ROOT="$FOODCAL_DIR"
export DUSTER_ROOT="$FOODCAL_DIR/duster"
export HF_HOME="${HF_HOME:-/workspace/.cache/huggingface}"
export TRANSFORMERS_CACHE="${HF_HOME:-/workspace/.cache/huggingface}"
EOF
ln -sf "$ENV_FILE" "$HOME/.foodcal_env" 2>/dev/null || true

echo ""
echo "Next:"
echo "  source $ENV_FILE"
echo "  bash scripts/runpod_benchmark.sh"
