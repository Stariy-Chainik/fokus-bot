#!/usr/bin/env bash
# Онлайн-бэкап SQLite базы SchedulePilot через Python sqlite3.Connection.backup()
# (безопасно при режиме WAL, не требует отдельного бинарника sqlite3).
# Пример cron на VPS (раз в сутки в 03:15):
#   15 3 * * * /opt/fokus-bot/scripts/backup_schedule_db.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"

if [[ -z "${SCHEDULE_PILOT_DB:-}" && -f "$ENV_FILE" ]]; then
  SCHEDULE_PILOT_DB="$(grep -E '^SCHEDULE_PILOT_DB=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d ' \r"'\''')"
fi

DB_PATH="${SCHEDULE_PILOT_DB:-/var/lib/fokus-bot/schedule-pilot.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fokus-bot}"
KEEP="${BACKUP_KEEP:-14}"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="python3"

if [[ ! -f "$DB_PATH" ]]; then
  echo "SQLite DB not found: $DB_PATH (skip)" >&2
  exit 0
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
TMP_DB="$BACKUP_DIR/schedule-pilot-${STAMP}.sqlite3"
OUT_GZ="${TMP_DB}.gz"

"$PYTHON" -c '
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
with sqlite3.connect(src_path, timeout=15.0) as src, sqlite3.connect(dst_path) as dst:
    src.backup(dst)
' "$DB_PATH" "$TMP_DB"

gzip -f "$TMP_DB"
chmod 600 "$OUT_GZ"

# Оставляем только последние $KEEP архивов
ls -1t "$BACKUP_DIR"/schedule-pilot-*.sqlite3.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "✓ backup created: $OUT_GZ"
