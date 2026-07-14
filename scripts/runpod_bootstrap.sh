#!/usr/bin/env bash
# RunPod 一鍵部署 foodcal
#
# 在 RunPod Web Terminal 貼上以下指令（先替換 HF_TOKEN）：
#
#   export HF_TOKEN="hf_你的token"
#   curl -fsSL https://raw.githubusercontent.com/WayneChen983/foodcal/main/scripts/runpod_bootstrap.sh | bash
#
# 或若已 clone 到 /workspace/foodcal：
#
#   export HF_TOKEN="hf_你的token"
#   bash /workspace/foodcal/scripts/runpod_bootstrap.sh

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPO_URL="${REPO_URL:-https://github.com/WayneChen983/foodcal.git}"
FOODCAL_DIR="${FOODCAL_DIR:-$WORKSPACE/foodcal}"

echo "=============================================="
echo " foodcal RunPod bootstrap"
echo " Workspace: $WORKSPACE"
echo "=============================================="

mkdir -p "$WORKSPACE/.cache/huggingface" "$WORKSPACE/.cache/torch" "$WORKSPACE/.cache/pip"

export HF_HOME="$WORKSPACE/.cache/huggingface"
export TRANSFORMERS_CACHE="$WORKSPACE/.cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$WORKSPACE/.cache/huggingface"
export TORCH_HOME="$WORKSPACE/.cache/torch"
export PIP_CACHE_DIR="$WORKSPACE/.cache/pip"
export FOODCAL_DIR="$FOODCAL_DIR"
export MINICONDA_INSTALL_DIR="$WORKSPACE/miniconda3"

if [[ ! -d "$FOODCAL_DIR/.git" ]]; then
  echo "[bootstrap] Cloning foodcal..."
  git clone "$REPO_URL" "$FOODCAL_DIR"
fi

cd "$FOODCAL_DIR"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo ""
  echo "[ERROR] 請先設定 HuggingFace Token："
  echo "  export HF_TOKEN=\"hf_xxxx\""
  echo ""
  echo "並到 https://huggingface.co/facebook/sam3 申請模型存取權"
  exit 1
fi

bash scripts/cloud_setup.sh

echo ""
echo "=============================================="
echo " RunPod 部署完成！執行範例："
echo "   source ~/.foodcal_env"
echo "   conda activate foodcal"
echo "   bash scripts/run_example.sh"
echo ""
echo " 啟動 Web API（給 App 用）："
echo "   bash scripts/runpod_webapp.sh"
echo "   → RunPod Connect → HTTP Port 8000 → 複製 proxy URL"
echo "   → 本機執行 webapp/run_cloud_connect.bat 貼上 URL"
echo ""
echo " 跑完記得在 RunPod 控制台 Terminate Pod 省錢"
echo " Network Volume 會保留模型 cache，下次開機更快"
echo "=============================================="
