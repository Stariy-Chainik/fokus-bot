# Перенос проекта на другой компьютер

Код живёт на GitHub: https://github.com/Stariy-Chainik/fokus-bot (ветка `main`). Там нет секретов и отчётов
с именами учеников — они лежат только локально и в резервном архиве.

## Что где лежит

| Что | Где | В git |
|---|---|---|
| Код, тесты, документация, фронт кабинета | репозиторий | да |
| `.env` (токены ботов, ключ Google, ЮКасса), `.env.prod` | корень проекта; копия боевого `.env` — на сервере в `/root/env-backups/` | **нет** |
| Данные школы | Google-таблица (`SPREADSHEET_ID` в `.env`) | нет |
| Боевой сервер | Hetzner, `root@178.104.240.252`, `/opt/fokus-bot`, systemd `fokus-bot` | нет |
| Локальные серверы кабинета для Claude Code | `.claude/launch.json` | нет |
| Отчёты с именами учеников | `docs/CRM_AUDIT_2026-10.md`, `docs/pakhomova_*` | нет, не коммитить |
| Памятка Claude Code о проекте | `~/.claude/projects/-Users-stas-Documents-fokus-bot/memory/` | нет |

## Резервный архив

`scripts/backup_local.sh` собирает `~/Documents/fokus-bot-backup-<дата>.tar.gz`: весь проект вместе с `.git`,
`.env`, `.env.prod`, `.claude/launch.json`, неотслеживаемыми отчётами и копией памятки Claude (`claude-memory/`).
Исключены `.venv`, `web/node_modules`, `web/.next`, кеши и журнал. **В архиве есть токены** — хранить только у себя,
не класть в общие папки и чаты.

## Развернуть на новом компьютере

1. Код: `git clone https://github.com/Stariy-Chainik/fokus-bot.git` (или распаковать архив — `.git` внутри).
2. Python 3.12 (как на проде): `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`.
3. Из архива положить в корень `.env`, `.env.prod`, папку `.claude/` (без `worktrees`). Без архива: `.env` — по `.env.example`,
   боевые значения — на сервере в `/opt/fokus-bot/.env`.
4. Доступ к серверу для `./scripts/deploy.sh` и журнала: добавить свой ssh-ключ в `/root/.ssh/authorized_keys`
   на `178.104.240.252` (со старого компьютера: `ssh-copy-id root@178.104.240.252`).
5. Проверка: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check bot config tests scripts`, `.venv/bin/mypy`;
   кабинет на живых данных — `MINIAPP_DEV_TG_ID=<tg_id> .venv/bin/python scripts/miniapp_dev.py` → http://localhost:8090/app/?dev=1.
6. Claude Code: папку `claude-memory/` из архива скопировать в `~/.claude/projects/<имя>/memory/`, где `<имя>` — путь к проекту
   с `/` заменёнными на `-` (для `/Users/stas/Documents/fokus-bot` это `-Users-stas-Documents-fokus-bot`).

Правила работы с продом — в [CLAUDE.md](../CLAUDE.md): деплой только `./scripts/deploy.sh`, не чаще раза в 3 минуты,
после него смотреть `journalctl -u fokus-bot` («Прогрев кеша», ERROR, 429); перед правкой `.env` на сервере —
копия в `/root/env-backups/`.
