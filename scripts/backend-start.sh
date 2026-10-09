#!/usr/bin/env bash
# VIGILIA — start the backend detached so it survives the launching shell.
# Usage: ./scripts/backend-start.sh   (logs to /tmp/vigilia-backend.log)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-3000}"
LOG="${VIGILIA_LOG:-/tmp/vigilia-backend.log}"

cd "$ROOT"

# Already up? Verify, and reuse it.
if curl -fsS -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "backend already healthy on :${PORT}"
  exit 0
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export NODE_ENV="${NODE_ENV:-development}"
export PORT
export HOST="${HOST:-0.0.0.0}"

setsid nohup npm run dev --workspace backend >"$LOG" 2>&1 < /dev/null &
disown || true

# Wait for readiness (max ~30s), do not block forever.
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "backend healthy on :${PORT} (pid $!)"
    exit 0
  fi
  sleep 0.5
done

echo "backend failed to become healthy — see $LOG" >&2
tail -20 "$LOG" >&2 || true
exit 1
