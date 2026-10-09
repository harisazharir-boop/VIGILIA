#!/usr/bin/env bash
set -euo pipefail
UNIT_DIR="${VIGILIA_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
sudo systemctl disable --now vigilia-backend.service 2>/dev/null || true
sudo systemctl disable --now vigilia-llama.service 2>/dev/null || true
systemctl --user disable --now vigilia-agent.service 2>/dev/null || true
sudo rm -f "$UNIT_DIR/vigilia-backend.service" "$UNIT_DIR/vigilia-llama.service"
rm -f "$USER_UNIT_DIR/vigilia-agent.service"
sudo systemctl daemon-reload
systemctl --user daemon-reload
printf '%s\n' 'Units removed. Environment files, token/state and install root were retained intentionally.'
