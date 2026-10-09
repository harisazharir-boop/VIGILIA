#!/usr/bin/env bash
# Non-destructive SQLite migration: consistent backup, migrate deploy, preflight.
set -euo pipefail
ROOT="${VIGILIA_INSTALL_ROOT:-/opt/vigilia}"
BACKEND_ENV="${VIGILIA_BACKEND_ENV:-/etc/vigilia/backend.env}"
BACKUP_DIR="${VIGILIA_DB_BACKUP_DIR:-/var/lib/vigilia/backups}"
RETAIN="${VIGILIA_DB_BACKUP_RETAIN:-7}"
[[ "$RETAIN" =~ ^[1-9][0-9]*$ ]] || { echo 'invalid backup retention' >&2; exit 1; }
[[ -r "$BACKEND_ENV" ]] || { echo 'backend environment unreadable' >&2; exit 1; }
set -a; . "$BACKEND_ENV"; set +a
[[ "${DATABASE_URL:-}" == file:* ]] || { echo 'only sqlite file DATABASE_URL supported' >&2; exit 1; }
DB="${DATABASE_URL#file:}"; [[ "$DB" = /* ]] || DB="$ROOT/$DB"
DB="$(realpath -m "$DB")"; ROOT_REAL="$(realpath -m "$ROOT")"
[[ "$DB" == "$ROOT_REAL"/* ]] || { echo 'database outside install root refused' >&2; exit 1; }
[[ -f "$DB" ]] || { echo 'database missing; initialize with prisma migrate deploy during first install' >&2; exit 1; }
mkdir -p "$BACKUP_DIR"; chmod 0750 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"; BACKUP="$BACKUP_DIR/prod-$STAMP.db"
python3 "$ROOT/deploy/sqlite-backup.py" "$DB" "$BACKUP"; chmod 0640 "$BACKUP"
DATABASE_URL="file:$DB" "$ROOT/node_modules/.bin/prisma" migrate deploy --schema "$ROOT/backend/prisma/schema.prisma"
DATABASE_URL="file:$DB" node "$ROOT/deploy/prisma-preflight.mjs"
mapfile -t OLD < <(ls -1t "$BACKUP_DIR"/prod-*.db 2>/dev/null | tail -n +$((RETAIN + 1)))
for file in "${OLD[@]}"; do rm -f -- "$file"; done
printf '%s\n' "migration completed; backup=$BACKUP"
