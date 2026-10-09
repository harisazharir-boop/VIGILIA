#!/usr/bin/env bash
# Start the Linux agent detached against the local backend.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pkill -9 -f "agents/linux/main.py" 2>/dev/null || true
sleep 1
cd "$ROOT"
export VIGILIA_BACKEND_URL="${VIGILIA_BACKEND_URL:-http://127.0.0.1:3000}"
export VIGILIA_AGENT_NAME="${VIGILIA_AGENT_NAME:-VIGILIA Linux Test}"
export VIGILIA_TELEMETRY_INTERVAL="${VIGILIA_TELEMETRY_INTERVAL:-5}"
export VIGILIA_HEARTBEAT_INTERVAL="${VIGILIA_HEARTBEAT_INTERVAL:-5}"
export VIGILIA_SECURITY_INTERVAL="${VIGILIA_SECURITY_INTERVAL:-5}"
export VIGILIA_STATE_DIR="${VIGILIA_STATE_DIR:-$ROOT/.vigilia}"
setsid nohup python3 agents/linux/main.py >"${VIGILIA_AGENT_LOG:-/tmp/vigilia-agent.log}" 2>&1 </dev/null &
disown || true
echo "agent started"
