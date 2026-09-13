# AGENTS.md

Инструкции для Codex и других AI-агентов, работающих в этом репозитории.

**Единственный источник правды по архитектуре, правилам домена и командам — [CLAUDE.md](CLAUDE.md).**
Этот файл раньше дублировал его и отставал (три роли вместо четырёх, 14 репозиториев, старая версия aiogram);
чтобы описание не расходилось, здесь остаются только различия, которых сейчас нет.

Кратко, что обязательно:

- Читать `CLAUDE.md` целиком перед изменениями; бизнес-правила — `docs/BUSINESS_RULES.md`;
  открытые дефекты — `docs/FOUND_BUGS.md` (не чинить молча), идеи — `docs/IMPROVEMENTS.md`.
- Проверки: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check bot config tests scripts`, `.venv/bin/mypy`.
- Тексты экранов, callback-строки и схема Google Sheets не меняются без отдельного решения; golden-снимки
  в `tests/golden/` пересъёмка только осознанно (`UPDATE_GOLDEN=1`).
- Деплой: `./scripts/deploy.sh` (rsync на VPS + restart), логи — `journalctl -u fokus-bot`.
