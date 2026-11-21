#!/usr/bin/env bash
# Deploy fokus-bot to Hetzner VPS (Nuremberg).
# Usage: ./scripts/deploy.sh [--dirty] [--skip-checks]
set -euo pipefail

HOST="root@178.104.240.252"
REMOTE="/opt/fokus-bot/"
SERVICE="fokus-bot"

ALLOW_DIRTY=0
SKIP_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    --dirty) ALLOW_DIRTY=1 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

if [[ "$ALLOW_DIRTY" -eq 0 ]]; then
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "✗ Есть незакоммиченные изменения в отслеживаемых файлах (закоммитьте или передайте --dirty)." >&2
    git status --short --untracked-files=no >&2
    exit 1
  fi
fi

if [[ "$SKIP_CHECKS" -eq 0 ]]; then
  echo "→ pre-flight: ruff, mypy, pytest"
  .venv/bin/ruff check bot config tests scripts
  .venv/bin/mypy
  GOLDEN_STRICT=1 .venv/bin/python -m pytest -q
fi

echo "→ rsync to $HOST:$REMOTE"
rsync -avz --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.claude' \
  --exclude='.vscode' \
  --exclude='.mypy_cache' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='bot.log' \
  --exclude='.env' \
  --exclude='credentials.json' \
  --exclude='token.json' \
  -e ssh ./ "$HOST:$REMOTE"

echo "→ restart $SERVICE"
ssh "$HOST" "systemctl restart $SERVICE && sleep 3 && systemctl is-active $SERVICE"

echo "→ health check"
ssh "$HOST" '
PORT=$(grep -E "^PAYMENT_WEBHOOK_PORT=" /opt/fokus-bot/.env 2>/dev/null | cut -d= -f2 | tr -d " \r" || true)
PORT=${PORT:-8081}
for i in $(seq 1 15); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "✓ /health ok (port ${PORT}, ${i}s)"
    exit 0
  fi
  sleep 1
done
echo "⚠ /health check failed on port ${PORT} after 15s" >&2
exit 1
'

echo "→ last 20 log lines"
ssh "$HOST" "journalctl -u $SERVICE -n 20 --no-pager"

echo "✓ deploy done"
