# fokus-bot

Telegram-CRM танцевальной школы «Фокус»: учёт занятий, группы и ученики,
зарплаты педагогов, счета родителям, оплаты и финансовая сводка.

Текущая версия — Python-бот на `aiogram 3` с Google Sheets в роли базы данных.
Проект параллельно подготавливается к переносу домена в Telegram Mini App и
веб-приложение на Next.js + PostgreSQL.

## Возможности

- Администратор: педагоги, ученики, филиалы и группы, счета, должники,
  зарплаты, прибыль, диагностика и запись занятий за педагога.
- Педагог: запись и просмотр занятий, группы, пары/солисты, статистика и
  сдача периода.
- Родитель: регистрация детей, занятия, счета и оплата.
- Биллинг групп: `NONE`, `PER_VISIT`, `SUBSCRIPTION` с помесячными
  переопределениями цены.
- Оплата: наличные, реквизиты, СБП, ЮКасса; опциональная фискализация через
  CloudKassir.

## Стек

| Слой | Технология |
|---|---|
| Бот | Python 3.12+, aiogram 3.13 |
| Данные | Google Sheets, gspread 6 |
| Настройки | pydantic-settings 2 |
| FSM | Redis в production или MemoryStorage без `REDIS_URL` |
| Платежи | ЮКасса, CloudKassir |
| Production | Hetzner VPS, systemd, polling |

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -m bot
```

Обязательные переменные:

- `BOT_TOKEN`;
- `GOOGLE_CREDENTIALS_JSON` — JSON сервисного аккаунта одной строкой;
- `SPREADSHEET_ID`.

Остальные параметры, включая имена листов, способы оплаты, Redis и webhook,
описаны в [.env.example](.env.example) и [config/settings.py](config/settings.py).

## Проверка

```bash
.venv/bin/python -m pytest -q
```

Для production достаточно `requirements.txt`; `requirements-dev.txt` дополнительно
устанавливает только инструменты проверки.

Тесты фиксируют формулы биллинга и прибыли, правила сервисов, webhook ЮКассы,
работу локеров и связность callback-кнопок. После изменения пользовательского
сценария дополнительно проверьте его вручную в Telegram.

## Архитектура

```text
bot/
  __main__.py       DI-контейнер и запуск polling/webhook
  handlers/         Telegram-представление и FSM по ролям
  services/         бизнес-правила и прикладные сценарии
  repositories/     доступ к листам Google Sheets
  models/           dataclass-сущности и enum
  keyboards/        inline-клавиатуры
  states/           FSM-состояния
  middlewares/      авторизация и дедупликация update
  utils/            даты, ID, attendees, локеры
config/
  settings.py       ENV-конфигурация
tests/              характеризующие и модульные тесты
scripts/            deploy и служебные миграции/аудиты
docs/               правила домена и план Mini App
```

Главный принцип слоёв: хендлеры не должны дублировать денежные и доменные
расчёты. Telegram, будущий API и веб-интерфейс должны использовать одни и те же
сервисы/правила.

Виртуальные строки `Billing` не хранятся в отдельном листе: счёт и зарплата
вычисляются из `Lesson` и `Teacher`. Настройка `SHEET_BILLING` осталась только
для обратной совместимости и текущим DI не используется.

## Google Sheets

Используемые репозиториями листы:

- `users`, `teachers`, `students`, `lessons`;
- `student_period_payments`, `teacher_period_submissions`;
- `branches`, `groups`, `teacher_groups`, `student_groups`;
- `clients`, `student_requests`;
- `subscription_overrides`, `finance_entries`.

Первая строка каждого листа — заголовки. Репозитории кэшируют чтение на 300
секунд и сбрасывают кэш при записи.

Важно: `student.parent_tg_ids` записывается через `|`, а не через запятую —
русская локаль Google Sheets может преобразовать строку Telegram ID в число.

## Формулы

```text
зарплата = ставка × duration_min / 45

индивидуальный счёт = rate_for_student × duration_min / 45
```

Стоимость индивидуального занятия делится между 1–4 участниками поровну;
целый остаток получает первый. Для `PER_VISIT` стоимость хранится снапшотом в
`lesson.attendees`. Полные правила находятся в
[docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md).

## Production

Текущий production работает в polling-режиме под systemd на Hetzner VPS.

```bash
./scripts/deploy.sh
ssh root@178.104.240.252 journalctl -u fokus-bot -f
```

`Procfile` и `WEBHOOK_URL` сохранены как альтернативный режим, но Railway сейчас
не используется.

## Документация

- [CLAUDE.md](CLAUDE.md) — подробная архитектура и инвентарь функций.
- [AGENTS.md](AGENTS.md) — инструкции для Codex и других AI-агентов.
- [BUSINESS_RULES.md](docs/BUSINESS_RULES.md) — краткий источник бизнес-правил.
- [MINIAPP_SPEC.md](docs/MINIAPP_SPEC.md) — фронт-независимая спецификация домена.
- [MINIAPP_BUILD.md](docs/MINIAPP_BUILD.md) — технический план Mini App/web.
- [MINIAPP_AGENT_PROMPT.md](docs/MINIAPP_AGENT_PROMPT.md) — комплект передачи задачи агенту.
- [FOUND_BUGS.md](docs/FOUND_BUGS.md) — найденные дефекты и их статус.
- [IMPROVEMENTS.md](docs/IMPROVEMENTS.md) — согласованные кандидаты улучшений.

## ID

Читаемые последовательные ID сохраняются для совместимости данных:
`TCH-XXXX`, `STU-XXXX`, `LES-XXXXXX`, `PAY-XXXXXX`, `SUB-XXXXXX`,
`BRN-XXXX`, `GRP-XXXX`, `USR-XXXX`, `CLT-XXXX`, `FIN-XXXXXX`.
