#!/usr/bin/env bash
# 在雲端 GPU 上執行 foodcal 範例 pipeline
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOODCAL_DIR="${FOODCAL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

[[ -f "$HOME/.foodcal_env" ]] && source "$HOME/.foodcal_env"

cd "$FOODCAL_DIR"
conda activate "${CONDA_ENV:-foodcal}" 2>/dev/null || true

echo "Running master_pipeline with bundled example images..."
python - <<'PY'
import os
import sys

# 使用 repo 根目錄，避免硬編碼路徑
BASE = os.environ.get("FOODCAL_DIR", os.getcwd())
sys.path.insert(0, BASE)

# 動態 patch master_pipeline 的路徑
import master_pipeline as mp
mp.BASE_DIR = BASE
mp.DUSTER_ROOT = os.path.join(BASE, "duster")

img_names = ["1001.jpg", "1002.jpg", "1003.jpg"]
imgs = [os.path.join(BASE, f) for f in img_names]
missing = [p for p in imgs if not os.path.exists(p)]
if missing:
    print("Missing images:", missing)
    sys.exit(1)

print("Images:", imgs)
mp.run_master_pipeline(imgs, ref_idx=2)
PY

echo "Done. Check master_report_*.json in $FOODCAL_DIR"
