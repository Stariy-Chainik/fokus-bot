#!/usr/bin/env bash
# Deploy fokus-bot to Timeweb VPS.
# Usage: ./scripts/deploy.sh
set -euo pipefail

HOST="root@147.45.146.247"
REMOTE="/opt/fokus-bot/"
SERVICE="fokus-bot"

cd "$(dirname "$0")/.."

echo "→ rsync to $HOST:$REMOTE"
rsync -avz --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.claude' \
  --exclude='.vscode' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='bot.log' \
  --exclude='.env' \
  --exclude='credentials.json' \
  --exclude='token.json' \
  -e ssh ./ "$HOST:$REMOTE"

echo "→ restart $SERVICE"
ssh "$HOST" "systemctl restart $SERVICE && sleep 3 && systemctl is-active $SERVICE"

echo "→ last 20 log lines"
ssh "$HOST" "journalctl -u $SERVICE -n 20 --no-pager"

echo "✓ deploy done"
