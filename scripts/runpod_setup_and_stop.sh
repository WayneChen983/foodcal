#!/usr/bin/env bash
# 安装完成后自动 Stop Pod（需 RUNPOD_API_KEY + HF_TOKEN）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOODCAL_DIR="${FOODCAL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

cd "$FOODCAL_DIR"
bash scripts/cloud_setup.sh

echo ""
echo ">>> 安装完成，准备自动 Stop Pod ..."
bash scripts/runpod_stop_self.sh
