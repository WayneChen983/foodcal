#!/usr/bin/env bash
# foodcal 雲端 GPU 伺服器一鍵安裝腳本
# 適用：Ubuntu 22.04/24.04 + NVIDIA GPU（建議 24GB+ VRAM）
#
# 用法：
#   export HF_TOKEN="hf_xxxx"          # 必填：HuggingFace token（需先申請 SAM3 權限）
#   bash scripts/cloud_setup.sh
#
# 可選環境變數：
#   FOODCAL_DIR   專案目錄（預設：腳本所在 repo 根目錄）
#   CONDA_ENV     conda 環境名稱（預設：foodcal）
#   SKIP_CONDA    設為 1 則跳過 conda 安裝（已有 miniconda 時）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOODCAL_DIR="${FOODCAL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONDA_ENV="${CONDA_ENV:-foodcal}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
WORKSPACE="${WORKSPACE:-/workspace}"
MINICONDA_INSTALL_DIR="${MINICONDA_INSTALL_DIR:-${WORKSPACE}/miniconda3}"
ENV_FILE="${FOODCAL_ENV_FILE:-$HOME/.foodcal_env}"

# RunPod Network Volume：cache 放在 /workspace 以便下次開 Pod 重用
if [[ -d "$WORKSPACE" ]]; then
  mkdir -p "$WORKSPACE/.cache/huggingface" "$WORKSPACE/.cache/torch" "$WORKSPACE/.cache/pip"
  export HF_HOME="${HF_HOME:-$WORKSPACE/.cache/huggingface}"
  export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME}"
  export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME}"
  export TORCH_HOME="${TORCH_HOME:-$WORKSPACE/.cache/torch}"
  export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$WORKSPACE/.cache/pip}"
  ENV_FILE="$WORKSPACE/.foodcal_env"
fi

echo "=============================================="
echo " foodcal cloud setup"
echo " Project: $FOODCAL_DIR"
echo " Conda env: $CONDA_ENV"
echo "=============================================="

# ── 0. 基本檢查 ──────────────────────────────────────────────
if ! command -v nvidia-smi &>/dev/null; then
  echo "[ERROR] nvidia-smi not found. Please use a GPU instance with NVIDIA drivers."
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
echo ""

# ── 1. 系統套件 ──────────────────────────────────────────────
echo "[1/7] Installing system packages..."
APT_CMD="apt-get"
if [[ "$(id -u)" -ne 0 ]]; then
  APT_CMD="sudo apt-get"
fi
$APT_CMD update -qq
$APT_CMD install -y -qq \
  git wget curl build-essential \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  ffmpeg 2>/dev/null || echo "  (skipped some apt packages — may already exist)"

# ── 2. Miniconda ─────────────────────────────────────────────
if [[ "${SKIP_CONDA:-0}" != "1" ]] && ! command -v conda &>/dev/null; then
  echo "[2/7] Installing Miniconda..."
  if [[ ! -d "$MINICONDA_INSTALL_DIR" ]]; then
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$MINICONDA_INSTALL_DIR"
    rm /tmp/miniconda.sh
  fi
  # shellcheck disable=SC1091
  source "$MINICONDA_INSTALL_DIR/etc/profile.d/conda.sh"
else
  echo "[2/7] Using existing conda..."
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
fi

# ── 3. Conda 環境 + PyTorch ──────────────────────────────────
echo "[3/7] Creating conda env and installing PyTorch (CUDA 12.6)..."
# Miniconda 2024+ 需先接受 channel ToS（非互動環境會失敗）
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>/dev/null || true
if conda env list | grep -q "^${CONDA_ENV} "; then
  echo "  Env '$CONDA_ENV' already exists, activating..."
else
  conda create -y -n "$CONDA_ENV" "python=${PYTHON_VERSION}"
fi
conda activate "$CONDA_ENV"

pip install --upgrade pip wheel setuptools
pip install torch==2.7.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126

python - <<'PY'
import torch
print(f"PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
PY

# ── 4. SAM3 ──────────────────────────────────────────────────
echo "[4/7] Installing SAM3..."
cd "$FOODCAL_DIR"
pip install -e .
pip install -e ".[notebooks]"

# ── 5. DUSt3R + 其他 Python 依賴 ─────────────────────────────
echo "[5/7] Installing DUSt3R and pipeline dependencies..."
pip install -r duster/requirements.txt
pip install \
  transformers accelerate bitsandbytes \
  qwen-vl-utils \
  opencv-python-headless \
  scipy matplotlib pillow tqdm

# qwen-vl-utils 等可能拉高 numpy 至 2.x，SAM3 需要 numpy<2
echo "[5b] Pinning numpy + setuptools for SAM3..."
pip install --ignore-installed --no-deps "numpy>=1.26,<2"
pip install "setuptools>=69"
python - <<'PY'
import numpy as np
v = tuple(int(x) for x in np.__version__.split(".")[:2])
assert v < (2, 0), f"numpy {np.__version__} too new for sam3"
print(f"  numpy {np.__version__} OK")
PY
echo "[6/7] HuggingFace authentication..."
if [[ -n "${HF_TOKEN:-}" ]]; then
  hf auth login --token "$HF_TOKEN" --add-to-git-credential 2>/dev/null \
    || huggingface-cli login --token "$HF_TOKEN" --add-to-git-credential
  echo "  HF token configured."
else
  echo "  [WARN] HF_TOKEN not set."
  echo "  You must run: hf auth login"
  echo "  And request access at: https://huggingface.co/facebook/sam3"
fi

# ── 7. 環境變數與驗證 ────────────────────────────────────────
echo "[7/7] Writing env helper and running smoke test..."

cat > "$ENV_FILE" <<EOF
# Source before running foodcal:  source $ENV_FILE
export FOODCAL_DIR="$FOODCAL_DIR"
export SAM3_ROOT="$FOODCAL_DIR"
export DUSTER_ROOT="$FOODCAL_DIR/duster"
export HF_HOME="${HF_HOME:-}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-}"
EOF

# 加入 bashrc（若尚未加入）
if ! grep -q "foodcal_env" "$HOME/.bashrc" 2>/dev/null; then
  echo "[[ -f \"$ENV_FILE\" ]] && source \"$ENV_FILE\"" >> "$HOME/.bashrc"
fi
ln -sf "$ENV_FILE" "$HOME/.foodcal_env" 2>/dev/null || true

python - <<'PY'
import importlib
mods = ["torch", "sam3", "transformers", "cv2", "scipy"]
for m in mods:
    importlib.import_module(m)
    print(f"  OK  {m}")
print("\nBasic imports passed.")
PY

echo ""
echo "=============================================="
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "   conda activate $CONDA_ENV"
echo "   source ~/.foodcal_env"
echo "   cd $FOODCAL_DIR"
echo ""
echo " Test pipeline (example images included):"
echo "   python master_pipeline.py"
echo ""
echo " Upload your photos:"
echo "   scp 1001.jpg 1002.jpg 1003.jpg ubuntu@YOUR_IP:$FOODCAL_DIR/"
echo "=============================================="
