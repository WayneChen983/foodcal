#!/usr/bin/env bash
# 排程在 N 秒後自動 Stop Pod（成功/失敗都會關，省得忘記）
#
# 用法（開 Pod 後先跑這個，再跑安裝）：
#   export RUNPOD_API_KEY="rpa_xxxx"
#   bash scripts/runpod_schedule_stop.sh        # 預設 1 小時
#   bash scripts/runpod_schedule_stop.sh 1800   # 30 分鐘
#   bash scripts/runpod_schedule_stop.sh 7200   # 2 小時
#
# 取消排程：
#   bash scripts/runpod_schedule_stop.sh --cancel

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${RUNPOD_STOP_PID_FILE:-/workspace/.runpod_scheduled_stop.pid}"
LOG_FILE="${RUNPOD_STOP_LOG_FILE:-/workspace/scheduled_stop.log}"

_cancel() {
  if [[ -f "$PID_FILE" ]]; then
    old_pid="$(cat "$PID_FILE")"
    if kill -0 "$old_pid" 2>/dev/null; then
      kill "$old_pid" 2>/dev/null || true
      echo "已取消排程 Stop（PID $old_pid）"
    fi
    rm -f "$PID_FILE"
  else
    echo "沒有進行中的排程 Stop"
  fi
}

if [[ "${1:-}" == "--cancel" ]]; then
  _cancel
  exit 0
fi

SECONDS="${1:-3600}"
API_KEY="${RUNPOD_API_KEY:-}"
POD_ID="${RUNPOD_POD_ID:-}"

if [[ -z "$API_KEY" ]]; then
  echo "[ERROR] 請先 export RUNPOD_API_KEY=\"rpa_xxxx\""
  exit 1
fi
if [[ -z "$POD_ID" ]]; then
  echo "[ERROR] RUNPOD_POD_ID 未設定"
  exit 1
fi

_cancel  # 取消舊排程

nohup env RUNPOD_API_KEY="$API_KEY" RUNPOD_POD_ID="$POD_ID" bash -c "
  echo \"[\$(date -Iseconds)] 排程 Stop 啟動：${SECONDS} 秒後關機\" >> '$LOG_FILE'
  sleep ${SECONDS}
  echo \"[\$(date -Iseconds)] 執行排程 Stop...\" >> '$LOG_FILE'
  bash '$SCRIPT_DIR/runpod_stop_self.sh' >> '$LOG_FILE' 2>&1 || true
" >/dev/null 2>&1 &

echo $! > "$PID_FILE"
mins=$((SECONDS / 60))
echo ">>> 已排程：約 ${mins} 分鐘後自動 Stop Pod"
echo ">>> 日誌：$LOG_FILE"
echo ">>> 取消：bash scripts/runpod_schedule_stop.sh --cancel"
