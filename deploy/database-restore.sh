#!/usr/bin/env bash
# Explicit SQLite restore. Backend must be stopped by operator before invocation.
set -euo pipefail
ROOT="${VIGILIA_INSTALL_ROOT:-/opt/vigilia}"
BACKEND_ENV="${VIGILIA_BACKEND_ENV:-/etc/vigilia/backend.env}"
BACKUP_DIR="${VIGILIA_DB_BACKUP_DIR:-/var/lib/vigilia/backups}"
SOURCE="${1:?usage: database-restore.sh /var/lib/vigilia/backups/prod-*.db}"
[[ -r "$BACKEND_ENV" ]] || { echo 'backend environment unreadable' >&2; exit 1; }
set -a; . "$BACKEND_ENV"; set +a
[[ "${DATABASE_URL:-}" == file:* ]] || { echo 'only sqlite file DATABASE_URL supported' >&2; exit 1; }
DB="${DATABASE_URL#file:}"; [[ "$DB" = /* ]] || DB="$ROOT/$DB"; DB="$(realpath -m "$DB")"
SOURCE="$(realpath -m "$SOURCE")"; BACKUP_REAL="$(realpath -m "$BACKUP_DIR")"
[[ "$SOURCE" == "$BACKUP_REAL"/prod-*.db && -f "$SOURCE" ]] || { echo 'backup path refused' >&2; exit 1; }
[[ "$(systemctl is-active vigilia-backend.service 2>/dev/null || true)" != active ]] || { echo 'backend must be stopped before restore' >&2; exit 1; }
mkdir -p "$BACKUP_DIR"; BEFORE="$BACKUP_DIR/pre-restore-$(date -u +%Y%m%d-%H%M%S).db"
[[ -f "$DB" ]] && python3 "$ROOT/deploy/sqlite-backup.py" "$DB" "$BEFORE"
TMP="$DB.restore.$$"; python3 "$ROOT/deploy/sqlite-backup.py" "$SOURCE" "$TMP"; mv -f "$TMP" "$DB"
DATABASE_URL="file:$DB" node "$ROOT/deploy/prisma-preflight.mjs"
printf '%s\n' "restore completed; previous backup=$BEFORE"
