#!/usr/bin/env bash
# 从 Pod 内部自动 Stop 自己（省 GPU 费用）
#
# 事前在 RunPod 取得 API Key：
#   https://www.runpod.io/console/user/settings
#
# 用法：
#   export RUNPOD_API_KEY="rpa_xxxx"
#   bash scripts/runpod_stop_self.sh

set -euo pipefail

POD_ID="${RUNPOD_POD_ID:-}"
API_KEY="${RUNPOD_API_KEY:-}"

if [[ -z "$POD_ID" ]]; then
  echo "[WARN] RUNPOD_POD_ID 未设定（通常 RunPod 会自动注入）"
  echo "  可在 Pod 详情页 URL 找到 Pod ID"
  exit 1
fi

if [[ -z "$API_KEY" ]]; then
  echo "[WARN] 未设定 RUNPOD_API_KEY，无法自动 Stop"
  echo "  请到 https://www.runpod.io/console/user/settings 建立 API Key"
  echo "  export RUNPOD_API_KEY=\"rpa_xxxx\""
  exit 1
fi

echo "Stopping pod ${POD_ID} ..."

curl -sf -X POST "https://api.runpod.io/graphql?api_key=${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { podStop(input: { podId: \\\"${POD_ID}\\\" }) { id desiredStatus } }\"}" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r)" 2>/dev/null \
  || echo "Stop request sent."

echo ""
echo ">>> Pod 将在数秒内 Stop，GPU 计费停止"
echo ">>> Network Volume 资料仍保留在 /workspace"
