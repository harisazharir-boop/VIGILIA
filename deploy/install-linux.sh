#!/usr/bin/env bash
# Install backend system service and invoking user's Wayland-safe agent service.
set -euo pipefail

ROOT="${VIGILIA_INSTALL_ROOT:-/opt/vigilia}"
BACKEND_USER="${VIGILIA_BACKEND_USER:-vigilia}"
BACKEND_ENV="${VIGILIA_BACKEND_ENV:-/etc/vigilia/backend.env}"
LLAMA_ENV="${VIGILIA_LLAMA_ENV:-/etc/vigilia/llama.env}"
UNIT_DIR="${VIGILIA_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${VIGILIA_AGENT_USER:-${SUDO_USER:-$USER}}"
SERVICE_UID="$(id -u "$SERVICE_USER")"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
USER_RUNTIME="/run/user/$SERVICE_UID"
AGENT_ENV="${VIGILIA_AGENT_ENV:-$SERVICE_HOME/.config/vigilia/agent.env}"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$SERVICE_HOME/.config}/systemd/user"

if [[ ! -f "$REPO/backend/dist/main.js" ]]; then
  printf '%s\n' 'Build first: npm run build' >&2; exit 1
fi
sudo install -d -m 0755 "$ROOT" "$(dirname "$BACKEND_ENV")" "$UNIT_DIR"
if ! id -u "$BACKEND_USER" >/dev/null 2>&1; then sudo useradd --system --home "$ROOT" --shell /usr/sbin/nologin "$BACKEND_USER"; fi
sudo install -d -o "$BACKEND_USER" -g "$BACKEND_USER" -m 0750 "$ROOT/models" "$ROOT/bin"
# Models, llama binaries and production SQLite state are externally managed.
# Preserve them across application deployments; rsync --delete must never
# remove a GGUF or target-only production database/WAL files.
sudo rsync -a --delete --exclude .git --exclude node_modules --exclude models --exclude bin \
  --exclude backend/prisma/prod.db --exclude backend/prisma/prod.db-wal --exclude backend/prisma/prod.db-shm \
  "$REPO/" "$ROOT/"
sudo chown -R "$BACKEND_USER:$BACKEND_USER" "$ROOT"
for script in database-migrate.sh database-restore.sh sqlite-backup.py prisma-preflight.mjs; do
  [[ -f "$ROOT/deploy/$script" ]] || { echo "required deploy artifact missing: $script" >&2; exit 1; }
done
sudo chmod 0750 "$ROOT/deploy/database-migrate.sh" "$ROOT/deploy/database-restore.sh"
sudo chmod 0640 "$ROOT/deploy/sqlite-backup.py" "$ROOT/deploy/prisma-preflight.mjs"
# Agent remains a graphical user service. Grant that user read/traverse access
# to deployed Python source without granting write access to backend files.
sudo setfacl -R -m u:"$SERVICE_USER":rX "$ROOT/agents"
sudo setfacl -R -d -m u:"$SERVICE_USER":rX "$ROOT/agents"
# Build-stage dependencies are needed only to run the pinned Prisma CLI and
# generate the native client. They are pruned before service enablement.
sudo -u "$BACKEND_USER" env HOME="$ROOT" npm ci --workspaces --include-workspace-root --prefix "$ROOT"
sudo -u "$BACKEND_USER" env HOME="$ROOT" sh -c "cd '$ROOT' && npx --no-install prisma generate --schema backend/prisma/schema.prisma"
if [[ ! -f "$BACKEND_ENV" ]]; then
  sudo install -o root -g "$BACKEND_USER" -m 0640 /dev/null "$BACKEND_ENV"
  sudo tee "$BACKEND_ENV" >/dev/null <<EOF
NODE_ENV=production
HOST=127.0.0.1
PORT=3000
DATABASE_URL=file:$ROOT/backend/prisma/prod.db
VIGILIA_CORS_ORIGIN=http://127.0.0.1:3000
LOG_LEVEL=info
EOF
fi
# Preserve contents but repair service-readable secret-file ownership/mode.
sudo chown root:"$BACKEND_USER" "$BACKEND_ENV"
sudo chmod 0640 "$BACKEND_ENV"
if [[ ! -f "$LLAMA_ENV" ]]; then
  sudo install -o root -g "$BACKEND_USER" -m 0640 /dev/null "$LLAMA_ENV"
  sudo tee "$LLAMA_ENV" >/dev/null <<EOF
VIGILIA_QWEN_GGUF=$ROOT/models/qwen2.5-3b-instruct-q4_k_m.gguf
VIGILIA_LOCAL_AI_MODEL_PATH=$ROOT/models/qwen2.5-3b-instruct-q4_k_m.gguf
VIGILIA_LOCAL_AI_MODEL=Qwen2.5-3B-Instruct
VIGILIA_LOCAL_AI_PORT=8080
VIGILIA_LOCAL_AI_THREADS=4
VIGILIA_LOCAL_AI_CONTEXT=2048
VIGILIA_LOCAL_AI_PARALLEL=1
VIGILIA_LOCAL_AI_MODEL_SHA256=
EOF
fi
sudo chown root:"$BACKEND_USER" "$LLAMA_ENV"
sudo chmod 0640 "$LLAMA_ENV"
# Database migration and connection preflight are explicit Phase 11C operations.
# Normal application deployment never creates, migrates or opens production DB.
sudo -u "$BACKEND_USER" env HOME="$ROOT" npm prune --omit=dev --workspaces --include-workspace-root --prefix "$ROOT"
sudo sed -e "s|/opt/vigilia|$ROOT|g" -e "s|/etc/vigilia/backend.env|$BACKEND_ENV|g" -e "/^User=/d" -e "/^Group=/d" "$REPO/deploy/systemd/vigilia-backend.service" | sudo tee "$UNIT_DIR/vigilia-backend.service" >/dev/null
sudo sed -i "/^Type=simple/a User=$BACKEND_USER\nGroup=$BACKEND_USER" "$UNIT_DIR/vigilia-backend.service"
sudo sed -e "s|/opt/vigilia|$ROOT|g" -e "s|/etc/vigilia/llama.env|$LLAMA_ENV|g" -e "s|User=vigilia|User=$BACKEND_USER|" -e "s|Group=vigilia|Group=$BACKEND_USER|" "$REPO/deploy/systemd/vigilia-llama.service" | sudo tee "$UNIT_DIR/vigilia-llama.service" >/dev/null
# The agent must be owned/configured by the interactive user, never root.
sudo -u "$SERVICE_USER" install -d -m 0700 "$USER_UNIT_DIR" "$(dirname "$AGENT_ENV")"
if [[ ! -f "$AGENT_ENV" ]]; then
  sudo -u "$SERVICE_USER" sh -c "umask 077; cat > '$AGENT_ENV'" <<EOF
VIGILIA_BACKEND_URL=http://127.0.0.1:3000
VIGILIA_AGENT_TOKEN_FILE=$SERVICE_HOME/.config/vigilia/agent.token
VIGILIA_STATE_DIR=$SERVICE_HOME/.local/state/vigilia
EOF
fi
sed "s|%h/vigilia|$ROOT|g" "$REPO/deploy/systemd/vigilia-agent.service" | sudo -u "$SERVICE_USER" tee "$USER_UNIT_DIR/vigilia-agent.service" >/dev/null
# Do not invent a bus. Enable only when the real user's login bus exists.
if [[ ! -S "$USER_RUNTIME/bus" ]]; then
  printf '%s\n' "No active user systemd bus for $SERVICE_USER at $USER_RUNTIME/bus; log in graphically, then rerun." >&2
  exit 1
fi
user_systemctl() { sudo -u "$SERVICE_USER" env XDG_RUNTIME_DIR="$USER_RUNTIME" DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME/bus" systemctl --user "$@"; }
user_systemctl daemon-reload
sudo systemctl daemon-reload
sudo systemctl enable vigilia-backend.service
sudo systemctl enable vigilia-llama.service
user_systemctl enable vigilia-agent.service
printf '%s\n' "Installed for $SERVICE_USER. Start with: sudo systemctl start vigilia-backend; user bus starts agent on login."
