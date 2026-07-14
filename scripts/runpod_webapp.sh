#!/usr/bin/env bash
# 在 RunPod GPU Pod 上啟動 FoodCal Web API（含完整推論管線）
#
# 前置：已執行 runpod_bootstrap.sh / cloud_setup.sh
#
# RunPod 控制台 → 你的 Pod → Connect → HTTP Port 8000
# 會得到類似：https://xxxxxxxx-8000.proxy.runpod.net
#
# 本機連線（二選一）：
#   A) 瀏覽器直接開上述 RunPod URL
#   B) 本機 proxy：set FOODCAL_REMOTE_API=上述 URL 後跑 webapp/run_cloud_connect.bat

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
FOODCAL_DIR="${FOODCAL_DIR:-$WORKSPACE/foodcal}"
WEBAPP_PORT="${WEBAPP_PORT:-8000}"

for env_file in "$WORKSPACE/.foodcal_env" "$HOME/.foodcal_env"; do
  if [[ -f "$env_file" ]]; then
    # shellcheck disable=SC1090
    source "$env_file"
    break
  fi
done

if command -v conda &>/dev/null; then
  conda activate "${CONDA_ENV:-foodcal}" 2>/dev/null || true
fi

cd "$FOODCAL_DIR"
export FOODCAL_DIR
unset FOODCAL_DEMO
unset FOODCAL_REMOTE_API

echo "=============================================="
echo " FoodCal Web API (GPU)"
echo " Directory: $FOODCAL_DIR"
echo " Port:      $WEBAPP_PORT"
echo "=============================================="

pip install -q -r webapp/requirements.txt

if command -v nvidia-smi &>/dev/null; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
  echo "[WARN] nvidia-smi not found"
fi

echo ""
echo ">>> 請到 RunPod 控制台：Connect → HTTP Port $WEBAPP_PORT"
echo ">>> 複製 proxy URL 到本機 webapp/run_cloud_connect.bat"
echo ""

exec python -m uvicorn webapp.server:app --host 0.0.0.0 --port "$WEBAPP_PORT"
