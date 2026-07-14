#!/usr/bin/env bash
# RunPod 上量測推論時間與成本（建議 RTX PRO 4500，約 $0.01–0.05 / 次）
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
FOODCAL_DIR="${FOODCAL_DIR:-$WORKSPACE/foodcal}"

for env_file in "$WORKSPACE/.foodcal_env" "$HOME/.foodcal_env"; do
  [[ -f "$env_file" ]] && source "$env_file" && break
done

conda activate "${CONDA_ENV:-foodcal}" 2>/dev/null || true
cd "$FOODCAL_DIR"
export FOODCAL_DIR
unset FOODCAL_DEMO

echo ">>> FoodCal benchmark on $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo unknown)"
python scripts/benchmark_pipeline.py --out "$FOODCAL_DIR/benchmark_report.json"
echo ">>> 跑完請 Stop Pod 停止計費"
