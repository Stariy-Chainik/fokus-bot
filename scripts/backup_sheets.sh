#!/usr/bin/env bash
# Ночной снапшот Google Sheets в /var/backups/fokus-bot/sheets-YYYYMMDD-HHMMSS.json.gz.
# Пример cron на VPS (раз в сутки в 03:30):
#   30 3 * * * /opt/fokus-bot/scripts/backup_sheets.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="python3"

cd "$APP_DIR"
exec "$PYTHON" "$APP_DIR/scripts/backup_sheets.py"
