#!/usr/bin/env bash
# Онлайн-бэкап SQLite базы SchedulePilot (безопасно при режиме WAL через sqlite3 .backup).
# Пример cron на VPS (раз в сутки в 03:15):
#   15 3 * * * /opt/fokus-bot/scripts/backup_schedule_db.sh
set -euo pipefail

DB_PATH="${SCHEDULE_PILOT_DB:-/var/lib/fokus-bot/schedule_pilot.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fokus-bot}"
KEEP="${BACKUP_KEEP:-14}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "SQLite DB not found: $DB_PATH (skip)" >&2
  exit 0
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
TMP_DB="$BACKUP_DIR/schedule_pilot-${STAMP}.db"
OUT_GZ="${TMP_DB}.gz"

sqlite3 "$DB_PATH" ".timeout 15000" ".backup '${TMP_DB}'"
gzip -f "$TMP_DB"
chmod 600 "$OUT_GZ"

# Оставляем только последние $KEEP архивов
ls -1t "$BACKUP_DIR"/schedule_pilot-*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "✓ backup created: $OUT_GZ"
