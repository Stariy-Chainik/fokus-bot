#!/usr/bin/env bash
# Резервный архив проекта для переноса на другой компьютер (docs/TRANSFER.md):
# код с историей git, .env/.env.prod, .claude/launch.json, неотслеживаемые отчёты и памятка Claude Code.
# Без .venv, node_modules, кешей и журнала. В архиве есть токены — хранить только у себя.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$ROOT")"
OUT="${1:-$HOME/Documents/$NAME-backup-$(date +%Y-%m-%d).tar.gz}"
MEM="$HOME/.claude/projects/$(echo "$ROOT" | tr '/' '-')/memory"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
[ -d "$MEM" ] && cp -R "$MEM" "$STAGE/claude-memory"
tar -czf "$OUT" \
  --exclude="$NAME/.venv" --exclude="$NAME/web/node_modules" --exclude="$NAME/web/.next" --exclude="$NAME/web/out" \
  --exclude="$NAME/.claude/worktrees" --exclude="$NAME/bot.log" --exclude='*/__pycache__' --exclude='*/.mypy_cache' \
  --exclude='*/.pytest_cache' --exclude='*/.ruff_cache' --exclude='*/.DS_Store' \
  -C "$(dirname "$ROOT")" "$NAME" \
  $( [ -d "$STAGE/claude-memory" ] && echo "-C $STAGE claude-memory" )
echo "архив: $OUT ($(du -h "$OUT" | cut -f1))"
