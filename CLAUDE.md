# CLAUDE.md

Guide for Claude Code (claude.ai/code) working in this repository. Project: **fokus-bot** — Telegram bot (aiogram 3) for a dance-school CRM, using **Google Sheets as the database**. Four roles: **admin**, **teacher**, **client (parent)**, **athlete (спортсмен — ученик со своим Telegram)**. Родитель дополнительно может работать через **бот в мессенджере MAX** (тот же процесс и данные, см. «MAX front»).

---

## Commands

```bash
# Run locally (polling mode)
python -m bot

# Deploy to production VPS (rsync + systemctl restart)
./scripts/deploy.sh

# Tail production logs
ssh root@178.104.240.252 journalctl -u fokus-bot -f

# Quick data lookup (example: list students)
.venv/bin/python3 -c "
import asyncio; from dotenv import load_dotenv; load_dotenv()
from config.settings import settings
from bot.repositories.sheets_client import SheetsClient
from bot.repositories.student_repo import StudentRepository
async def main():
    repo = StudentRepository(SheetsClient(settings), settings.sheet_students)
    for s in await repo.get_all(): print(s.student_id, s.name)
asyncio.run(main())"
```

Tests are run with `.venv/bin/python -m pytest -q` (characterization/unit tests, including callback wiring). Линт и типы: `.venv/bin/ruff check bot config tests scripts` и `.venv/bin/mypy` (конфиг в `pyproject.toml`; mypy нестрогий, `union-attr` отключён только для aiogram-слоёв). Golden-снимки экранов — `tests/golden/*.txt`, пересъёмка после намеренного изменения текста: `UPDATE_GOLDEN=1 .venv/bin/python -m pytest -q` (см. `tests/fakes.py`). After automated checks, manually exercise the affected Telegram flow; for production changes, inspect service logs.

Python 3.12+ (прод 3.12.3; локальный `.venv` тоже 3.12 — `maxapi` требует ≥ 3.10). Main deps: `aiogram 3.31`, `maxapi 1.2`, `gspread 6`, `pydantic 2`, `pydantic-settings`, `python-dotenv`, `redis`, `yookassa`, `qrcode[pil]`. Runtime list is in [requirements.txt](requirements.txt); test tooling is in [requirements-dev.txt](requirements-dev.txt).

---

## Architecture

### Entry point & wiring

`bot/__main__.py` is the single DI container. Order matters:

1. Build **`SheetsClient`** from settings (one shared gspread client, caches Worksheet handles).
2. Construct **14 repositories** (one per Google Sheets tab), all inheriting `BaseRepository`.
3. Construct **8 services**: `LessonService`, `PaymentService`, `ProfitService`, `DiagnosticsService`, `TeacherVisibilityService` (via [bot/services/visibility.py](bot/services/visibility.py)), `StudentService`, `StudentRequestService`, `CloudKassirService`. (`BillingService` is a pure-functions module — not instantiated.)
4. Pick **FSM storage**: `RedisStorage` if `REDIS_URL` is set, else `MemoryStorage`.
5. Register two middlewares (in this order):
   - **`DedupUpdateMiddleware`** (outer) — drops re-delivered Telegram updates by `m:{chat_id}:{message_id}` / `c:{callback_query.id}` key with 60 s TTL, GC after 256 entries.
   - **`AuthMiddleware`** (with `user_repo`) — loads `User` by `tg_id`, injects into `data["user"]` (or `None` for unknown). Also logs per-update latency.
6. Inject every repo / service into the dispatcher via `dp["key"]`. Handlers receive them as typed parameters — aiogram resolves the names automatically.
7. Include four top-level routers: `common_router`, `admin_router`, `teacher_router`, `client_router`.
8. Webhooks:
   - Telegram: `/webhook/{bot_token}` if `WEBHOOK_URL` is set, otherwise polling.
   - YooKassa: `/yookassa-webhook` always registered on `PAYMENT_WEBHOOK_PORT` (default 8081).

> **Прогрев кеша.** `_cache_warmer` (в `bot/__main__.py`, раз в 240 с при TTL кеша 300 с) фоном читает основные
> листы. Без него первый запрос после простоя читает Google Sheets по-настоящему — 8–10 секунд, и Mini App успевает
> отвалиться по таймауту, а родитель жмёт «оплатить» повторно.

### Telegram Mini App (кабинеты администратора и педагога)

Фронт `miniapp/` (`index.html` + `app.js`, без сборки) раздаётся aiohttp-сервером бота по `/app/`
([bot/api/static.py](bot/api/static.py)); API `/api/admin/*` — [bot/api/admin.py](bot/api/admin.py), тонкий слой над
`PaymentService`/`SalaryService`/`StudentService` (суммы считает сервер, один запрос = один экран).
Авторизация: `Authorization: tma <initData>` (подпись — `bot/utils/telegram_auth.py`) + `users.is_admin`;
локально — `MINIAPP_DEV_TG_ID` и заголовок `Authorization: dev` (`?dev=1` во фронте). Локальный сервер без
Telegram-поллинга: `MINIAPP_DEV_TG_ID=<tg_id> .venv/bin/python scripts/miniapp_dev.py` →
http://localhost:8090/app/?dev=1 (данные — живая таблица). Тесты — `tests/test_admin_api.py`. План этапов,
контракт API и что нужно для запуска в Telegram (домен + nginx + BotFather) — [docs/MINIAPP_PLAN.md](docs/MINIAPP_PLAN.md).
Прототип всех кабинетов — `web/miniapp-prototype.html`.

**Кабинет педагога** — `bot/api/teacher.py` (`/api/teacher/*`) + `miniapp/teacher.js` (экраны `t.*`):
свои занятия журналом по дням (день/месяц, фильтр «группы / индивидуальные», итог дня и месяца, карточка, удаление), запись занятия общим с админом мастером
(`bot/api/record.py`, но `bypass_period_lock=False`), мои группы с составом/парами/солистами,
карточка ученика (только видимые через `TeacherVisibilityService`), зарплата по строкам
`SalaryService` и сдача периода (правило «с 25-го»), дневники спортсменов (оценка 1–5 с пушем спортсмену и родителям, задания, рейтинг — через `DiaryService`) и счета своих групп для `BILLING_TEACHER_IDS` — внутри карточки группы вкладка «💳 Оплата» (ученики с суммами и остатком, рассылка счетов группе) и кнопка «🧾 Счёт» в карточке ученика; отдельного раздела «Счета групп» нет, чтобы не было второго пути к тому же ученику. Отправка родителям тем же `_send_bill_to_parents`, что у админа, отметка оплаты через `PaymentService.record_payment` (сумма, способ и «отметил педагог N» попадают в строку оплаты). Роль определяется на входе: `/api/admin/me`,
при 403 — `/api/teacher/me`; у кого обе роли — переключатель на сводке. Тесты — `tests/test_teacher_api.py`.

**Кабинет родителя** — `bot/api/parent.py` (`/api/parent/*`) + `miniapp/parent.js` (экраны `p.*`): сводка по детям
(`p.home`), счета по месяцам (`p.bills`, по умолчанию только неоплаченные) и сам счёт (`p.bill`): позиции
(педагог / абонемент) сгруппированы в разделы «Абонемент» / «Групповые занятия» / «Индивидуальные и парные»
с итогом каждого, отмечаются галочками, занятия внутри позиции свёрнуты и раскрываются тапом («2 занятия,
1 не оплачено — показать»; у занятия иконка 👥/👤), «К оплате»
пересчитывается на лету, по умолчанию отмечено всё неоплаченное. «Занятия» (`p.lessons`) — **расписание без финансов** (решение владельца 24.09.2026):
сводка месяца (занятий и часов), чипы фильтра по педагогам, календарь месяца с точками по занятиям (цвет —
группа/индивидуальное; тап по дню оставляет только его, повторный тап или «✕ Весь месяц» возвращает месяц) и
журнал по дням. Ни сумм, ни статусов оплаты — деньги только в счетах. `POST /pay`
принимает `keys` + `lessonIds`: позиция с отмеченными занятиями оплачивается на их сумму (намерение пишется в
строку-остаток через `set_payment_intent`), остальные — целиком. Занятия месяца (`p.lessons`) и дневник
ребёнка только на чтение (`p.diary`). Оплата: ЮКасса (карта/СБП, ссылка открывается `tg.openLink`, слежение —
`start_payment_watch`), наличные (уведомление админам, как в боте), реквизиты/СБП — текст + QR, чек родитель
присылает в бот. Родитель определяется по `students.parent_tg_ids`, видит только своих детей; имя в приветствии —
из карточки клиента. Вход: `/api/admin/me` → 403 → `/api/teacher/me` → 403 → `/api/parent/me`.
Тесты — `tests/test_parent_api.py`.

**Кабинет спортсмена** — `bot/api/athlete.py` (`/api/athlete/*`) + `miniapp/athlete.js` (экраны `s.*`): сводка
дневника (`s.home`), запись тренировки **одной формой** вместо мастера из пяти шагов (`s.log`: дата, минуты,
темы, задания педагога, комментарий), записи месяца с удалением неоценённых (`s.entries`), открытые задания
с «сделано N раз» (`s.tasks`) и рейтинг с фильтром по танцу и подсветкой своей строки (`s.rating`). Всё считает
`DiaryService`, поэтому очки и места совпадают с ботом; оценки ставит только педагог. Спортсмен определяется по
`students.athlete_tg_id`. Тесты — `tests/test_athlete_api.py`.

### MAX front (кабинет родителя в мессенджере MAX)

Второй мессенджер для **родителя** на тех же репозиториях/сервисах: пакет [bot/max/](bot/max/) (библиотека `maxapi`, Python ≥ 3.10). `bot/__main__.py` при непустом `MAX_BOT_TOKEN` собирает `maxapi.Dispatcher` (`bot/max/app.py::build`) и запускает polling отдельной задачей (`run_max`, перезапуск при сбое); пустой токен — MAX выключен, Telegram работает как прежде. Ключевые части:

- **Идентификация**: `students.parent_max_ids` (кол. 10, `|`), `clients.max_id` (кол. 7). Адрес родителя `Addr = ("tg"|"max", id)` — [bot/services/parent_notifier.py](bot/services/parent_notifier.py): `fmt_addr` (`123` для TG, `m456` для MAX) / `parse_addr`; `Student.parent_addrs`; `StudentRepository.get_by_parent/add_parent/remove_parent(addr)`.
- **ParentNotifier** (`dp["notifier"]`, `resolve_notifier(bot)`): единая доставка уведомлений родителям в оба мессенджера (счета, должники, отказ по чеку, ЮКасса-watcher, оценки, привязки, заявки). Кнопки в уведомлениях — нейтральные ряды из `bot/screens`.
- **Экраны без транспорта**: [bot/screens/](bot/screens/) (`Btn/cb/url`, `parent_menu.py`, `parent_bills.py`) + данные [bot/services/parent_views.py](bot/services/parent_views.py) (`bills_periods`, `bill_detail`, `unpaid_for`, `qr_png`, `admin_confirm_rows`, `receipt_caption`…). Telegram-хендлеры `client/my_bills/*` и MAX-хендлеры `bot/max/handlers/*` используют одни и те же callback-строки и билдеры; адаптеры — `bot/screens/adapters.py` (aiogram) и `bot/max/render.py` (MAX: `to_max_markup`, `send_screen`, `edit_screen`, `split_text` ≤ 4000).
- **MAX-хендлеры** получают зависимости по имени параметра из `DepsMiddleware` (`bot/max/middlewares.py`, копия `dp.workflow_data` + `max_uid`, `tg_bot`, `max_bot`), FSM — параметр `context`. Релиз 1: вход (`bot_started` с payload ссылки группы `g_…` — тот же payload, что для `t.me`; фамилия; второй ребёнок → одобрение админа в Telegram), меню (`menu_rows(platform="max")`: счета + добавить ребёнка), счета, оплата (СБП онлайн, реквизиты + QR-вложение + чек, наличные). Чек из MAX скачивается (`download_bytes`) и уходит админам **в Telegram** с кнопками `rcpp:`/`receipt_confirm:`/`rcpt_no:…:m<id>`; ответ родителю — через notifier. Занятия/дневник/email в MAX — релиз 2.
- Ссылки групп для MAX печатает `scripts/gen_group_links.py` (`https://max.ru/<bot>?start=g_…`) при заданном токене. Тесты: `tests/test_parent_screens.py`, `tests/test_parent_notifier.py`, `tests/test_max_front.py` (importorskip maxapi).

### Handlers

```
bot/handlers/
  common.py              # /start, /menu, mode:admin/teacher, go:home, noop
  admin/
    teachers/            # ПАКЕТ: _base, listing (список+карточка), groups (teg_*),
                         #   manage (add/del), periods (открытие), rates (ставки FSM)
    students/            # ПАКЕТ (P5-разбивка god-файла): _base (router+_render_student_card),
                         #   overview, listing, add, delete, partners, groups, requests, client
    client_requests.py   # approve/reject parent→student link requests (admin_child_ok/no)
    salaries.py          # teacher earnings by period (salary_teacher/period)
    bills/               # ПАКЕТ: _base (router+гарды), helpers (_send_bill_to_parents),
                         #   view (просмотр+рассылка группе), send, confirm (оплаты)
    profit/              # ПАКЕТ: _base (render DTO), overview, finance_entries (FSM fin:*),
                         #   daily; все расчёты выполняет ProfitService
    debtors.py           # «⚠️ Должники»: сводный контроль оплат + массовое напоминание
    branches/            # ПАКЕТ (P5): _base (router+_render_group_card), crud_branches,
                         #   groups, billing, members
    edit_lesson.py       # admin-only lesson view/delete (bypasses period lock)
    diagnostics.py       # consistency check (orphan lessons etc.)
    record_lesson.py     # proxy: admin records lessons on behalf of a teacher
  teacher/
    record_lesson/       # ПАКЕТ (P5): _base, flows, finalize (LessonService),
                         #   entry, schedule, group, soloist, pair, shared. FSM RecordLessonStates
    my_lessons/          # ПАКЕТ: _base, listing (фильтры/пагинация), detail (общая с admin),
                         #   deletion, guests (добавление в сохранённое групповое занятие)
    submit_period.py     # lock period for billing (allowed from 25th of month)
    my_groups/           # ПАКЕТ: _base (рендер карточки), roster, members, attendance
    partners/            # ПАКЕТ: _base, lists (пары/солисты), cards, partner_manage,
                         #   lessons_history
    my_stats.py          # personal earnings summary
  client/
    start.py             # client registration FSM (search → confirm) + admin approval
    my_lessons.py        # lesson history; ✅ on paid; per-child or "all"
    my_bills/            # ПАКЕТ: _base, viewing (счета), payment (методы/чек/подтверждение)
    payments.py          # YooKassa webhook (verified via API re-fetch) + Telegram Payments pre_checkout
```

> **P5-декомпозиция:** `admin/students`, `admin/branches`, `teacher/record_lesson` — теперь **пакеты**
> (каждый под-модуль экспортирует свой набор хендлеров на общий `router` из `_base`; порядок регистрации =
> порядок импорта в `__init__.py`). Публичный `router` пакета — как у обычного модуля.

Each handler file exports one `Router`. Aggregated in `bot/handlers/{admin,teacher,client}/__init__.py`, then in `bot/handlers/__init__.py`, registered in `bot/__main__.py`.

### Доступ по ролям, callback-данные, экраны

- **Роль — фильтр aiogram**, а не `if` в теле хендлера: [bot/handlers/filters.py](bot/handlers/filters.py) —
  `AdminOnly()`, `TeacherOnly()`, `TeacherOrAdmin()`, `BillingTeacherOnly()` в декораторе
  `@router.callback_query(F.data == "…", AdminOnly())`. При отказе фильтр сам показывает alert «Нет доступа»
  и возвращает `False`; catch-all callback-хендлеров нет, так что отклонённый callback никуда не проваливается
  (инвариант — `tests/test_guards_static.py`: каждый callback в `admin/`/`teacher/` защищён фильтром, предикатом в теле
  или FSM-состоянием). Гарды message-хендлеров (молчаливый `return`) остаются в теле. Предикаты —
  [bot/handlers/access.py](bot/handlers/access.py) (`TypeGuard`; `TeacherUser` — `User` с гарантированным `teacher_id`
  после `is_teacher`).
- **Callback-строки** разбираются именованными датаклассами [bot/utils/callbacks.py](bot/utils/callbacks.py)
  (`XCb.unpack(callback.data)` / `.pack()`), строки байт-в-байт прежние (roundtrip по корпусу литералов —
  `tests/test_callbacks.py`). Переведены самые насыщенные файлы (edit_lesson, branches/groups, my_bills/payment,
  bills/confirm, client/my_lessons, salaries); остальные `split(":")` — по мере правок. aiogram `CallbackData`
  не используется (пишет все поля — изменил бы строки).
- **Пагинация** — [bot/utils/paging.py](bot/utils/paging.py) (`paginate` → `Page`) + `keyboards/common.nav_row`;
  размеры страниц в `utils/constants.py`.
- **Экраны и карточки** — [bot/screens/](bot/screens/): родительские экраны (`parent_bills.py`, включая
  `render_bill_detail`), карточки (`cards.py`: `admin_student_card_view(StudentCard)`, `partner_label`).
  Хендлер получает DTO из сервиса и только показывает результат.
- **Состав группы** — `bot.services.rosters.group_members(student_repo, student_group_repo, group_id, key=…)`
  (ключ сортировки — параметр: `BY_NAME`, `BY_NAME_CI`, `None`); копий «member_ids ∩ get_all()» в хендлерах нет.
- **Справочники одним чтением**: в циклах не вызывать `repo.get_by_id` — `get_by_id` у всех репозиториев это
  линейный скан `get_all()` с пересборкой dataclass'ов; предзагружать `{id → entity}` (`get_all(include_archived=True)`,
  если раньше объект находился через `get_by_id`).

### Repositories

All inherit `BaseRepository` ([bot/repositories/base.py](bot/repositories/base.py)) which wraps gspread inside `asyncio.to_thread()` and provides:

- **TTL cache** (300 s) on every read; any write to that sheet invalidates the cache for that sheet.
- **Retry**: 3 attempts with exponential backoff on HTTP 429 / 503 / network errors.
- 1-based row indexing (row 1 = header, row 2+ = data).
- **Запись по ключу** (`async with self._locked_row(group_id=gid) as row_idx:`): per-sheet `asyncio.Lock` на «найти строку → записать/удалить» + перед записью ключевые ячейки строки перечитываются с живого листа (`row_values`, +1 API-вызов); при расхождении кеш сбрасывается и поиск повторяется, после второго промаха запись отменяется (`logger.error`). `_delete_all_where(**key)` — удаление всех строк по ключу. Тесты — `tests/test_repository_writes.py` (`FakeWorksheet` из `tests/fakes.py`).

| Repo class | Google Sheets tab | Purpose |
|---|---|---|
| `UserRepository` | `users` | tg_id → User; is_admin, optional teacher_id |
| `TeacherRepository` | `teachers` | Teacher cards + 3 rates (group / for_teacher / for_student) |
| `StudentRepository` | `students` | Students; partners (symmetric); parent_tg_ids (pipe-separated); client_id; tier |
| `LessonRepository` | `lessons` | All lessons; query by teacher/period/student; individual_lesson_exists guard |
| `PaymentRepository` | `student_payments` | `StudentPeriodPayment` rows; confirm single or batch-per-period; `lesson_ids` (кол. 15, `LES-…` через `|`) — за какие занятия принята оплата (у строки-остатка это намерение плательщика), `set_lesson_ids()` |
| `TeacherPeriodSubmissionRepository` | `teacher_submissions` | "Period submitted" rows — used as a lock |
| `BranchRepository` | `branches` | School branches |
| `GroupRepository` | `groups` | Groups; billing_mode + per-tier prices/durations + `archived` (кол. 12): архивная группа скрыта из списков (`get_all()` / `get_by_branch()` по умолчанию без них), но доступна по `get_by_id` и через `include_archived=True` — история занятий, счетов и зарплат не меняется |
| `TeacherGroupRepository` | `teacher_groups` | Many-to-many teacher ↔ group (join table) |
| `StudentGroupRepository` | `student_groups` | Many-to-many student ↔ group + `joined_period` (кол. 3) и `left_period` (кол. 4), `YYYY-MM`. Абонемент начисляется с месяца вступления и **до** месяца ухода (`StudentGroup.covers(period)`); пусто = «с начала» / «не ушёл». `add()` ставит текущий месяц и снимает пометку ухода у вернувшегося; `set_joined_period()` / `set_left_period()` правят задним числом; `get_membership_map()` → `{(sid, gid): StudentGroup}`. `get_students_for_group()` / `get_groups_for_student()` по умолчанию **без ушедших**, начисления зовут их с `include_left=True` |
| `ClientRepository` | `clients` | Parent entities; phone (normalized) + optional tg_id |
| `StudentRequestRepository` | `student_requests` | Teacher-submitted requests to add a new student (admin approves) |
| `FinanceEntryRepository` | `finance_entries` | Ручные доходы (турниры) / расходы (аренда) месяца — блоки на экране «Прибыль» |
| `TeacherPayoutRepository` | `teacher_payouts` | Факты выплаты зарплаты педагогам: `(teacher_id, period_month, amount, paid_at, paid_by)`; несколько строк на месяц = аванс + остаток |
| `SalaryOverrideRepository` | `salary_day_overrides` | Нестандартные дни: `(teacher_id, date, minutes, comment)` — минуты смены за дату, заменяют расчёт по группам |
| `TeacherRateHistoryRepository` | `teacher_rate_history` | История ставок: `until_period` — «ставки действуют по этот месяц включительно»; для периода берётся ближайшая граница ≥ period, иначе карточка педагога. Кэш в памяти (`bot/services/rate_history.py`), обновляется фоном раз в 5 мин |
| `TrainingEntryRepository` | `training_entries` | Дневник спортсмена: `(entry_id TE-, student_id, date, minutes, topics\|, task_ids\|, comment, grade 1–5, grade_comment, graded_by)`; оценку ставит педагог |
| `AthleteTaskRepository` | `athlete_tasks` | Задания педагога спортсмену: `(task_id TK-, student_id, teacher_id, exercise, minutes, comment, source teacher\|lecture, status open\|closed)`; открыто до закрытия педагогом |
| `PendingActionRepository` | `pending_actions` | Очередь решений администратора: `(action_id ACT-, kind cash\|receipt\|child, student_id, period_month, amount, method, parent_addr, file_id, status open\|done\|rejected)`. Пишется, когда родитель сообщает об оплате или присылает чек (бот, кабинет, MAX); закрывается любым решением — кнопкой в чате или в кабинете ([bot/services/pending_queue.py](bot/services/pending_queue.py)). **Идемпотентность:** кнопка уведомления несёт `action_id` (`pact:{id}:{сумма}:{код}` / `pnay:{id}`), перед зачислением `claim()` атомарно переводит строку `open → done` под замком листа с перечиткой статуса — сколько бы копий уведомления ни висело в чатах, зачтётся один раз. **Переплата:** если заявленная сумма больше остатка, подтверждение не проходит молча — бот показывает выбор «зачесть остаток / зачесть всё», кабинет отвечает 409 `needsConfirm` и спрашивает тем же вопросом |
| `SubscriptionOverrideRepository` | `subscription_overrides` | Переопределение цены абонемента на месяц: `(group_id, period, student_id?)` → amount; пустой student_id = вся группа |

**Google Sheets locale gotcha**: Russian-locale spreadsheets interpret `,` as a decimal separator. Any multi-value field written as comma-separated integers will be silently corrupted (`"123,456"` → `123.456` → `123`). Use `|` as separator. See `student.parent_tg_ids` (parser still accepts `,` for backwards compatibility).

### Services

| Service | Responsibility |
|---|---|
| `LessonService` | `create()` / `create_pair_batch()` / `create_soloist_batch()` / `delete()`. All accept `bypass_period_lock: bool` (admins pass `True`). Solo duplicates blocked via `individual_lesson_exists`; group duplicates intentionally **not** blocked (one group can have multiple shifts per day). `add_guest(lesson, student_id, group)` — гость в сохранённом групповом занятии (цена `price_full` PER_VISIT, иначе 0; B7). `can_submit_period(today, period)` — правило «сдать с 25-го». |
| `SalaryService` ([salary_service.py](bot/services/salary_service.py)) | Единая точка зарплаты педагога за период: `lines_for(teacher, period)` → строки (занятия / в смене / смена / корректировка), `total_for()`. Используется в «Зарплатах», «Выплатах», «Прибыли» (extra_salary) и превью сдачи периода. |
| `BillingService` ([billing_service.py](bot/services/billing_service.py)) | Pure functions only: `calc_earned()` (teacher salary) and `build_billing_rows()` (virtual per-student Billing rows, computed on demand from a Lesson + Teacher). |
| `PaymentService` | `compute_bills_for_student_period()` → `{teacher_id \| SUB:gid → BillAggregate(name, total, items, subscription)}`, `get_or_create_invoices_for_student_period()`, `confirm_period()` (batch PENDING → PAID), `confirm_payment()` (single), `confirm_teachers()`, `record_payment()`, `create_yookassa_payment()` (returns confirmation URL), `compute_debt_map()` + `build_debtor_rows()` → `DebtorRow` (экран «Должники»), `student_lesson_marks()` → `StudentMonthLessons` (экран «Занятия» родителя: доля ученика и ✅/⬜ считает `payment_ledger.mark_student_lessons`, не хендлер). Does **not** check submission status — admins can issue bills anytime. |
| `ProfitService` | Хранилище-независимые DTO и расчёт экрана/API «Прибыль»: строки педагогов и занятий, выручка, зарплата, маржа, абонементы, ручные доходы/расходы. `get_lesson_summary()`, `get_month_summary()`, `get_teacher_detail()`. Telegram-хендлер только форматирует результат. |
| `StudentService` | Бизнес-логика ученика поверх нескольких репо: `create_with_group()`, `delete_student()`, `toggle_tier()`, `pairs_in_group()` / `soloists_in_group()`, `partner_candidates()`, `get_student_card()` → `StudentCard` DTO (собирает карточку из 7 репо, чтобы хендлер только рисовал). |
| `StudentRequestService` | Обработка заявок педагогов на новых учеников: `approve_create()`, `approve_link_existing()` (→ `LinkExistingOutcome`). |
| `TeacherVisibilityService` | Who can see whom: `students_for_teacher()`, `students_in_group_for_teacher()`, `teachers_for_student()`, `is_visible()`. Pure intersection of `teacher_groups` and `student_groups`. |
| `DiaryService` ([diary_service.py](bot/services/diary_service.py)) | Кабинет спортсмена: записи тренировок, задания, оценки, статистика и рейтинг. Чистые функции `compute_stats()`, `compute_leaderboard()`: **очки = минуты × оценка** (без оценки — коэффициент 3), при фильтре по танцу минуты записи делятся поровну между её темами; места плотные. Темы — [bot/utils/diary_topics.py](bot/utils/diary_topics.py) (бальные танцы / предметы ХГ по названию группы). |
| `DiagnosticsService` | `run_consistency_check()` → `DiagnosticsReport` (orphan lessons referencing missing teachers/students). |
| `CloudKassirService` | `send_income_receipt(phone, student_name, period_month, amount)` — fires a fiscal receipt; phone is normalized to `+7…`. No-op if `CLOUDKASSIR_PUBLIC_ID` is empty. |

### Models & enums

Dataclasses in [bot/models/entities.py](bot/models/entities.py): `User`, `Teacher`, `Student`, `Lesson`, `Billing` (virtual), `StudentPeriodPayment`, `TeacherPeriodSubmission`, `Branch`, `Group`, `TeacherGroup`, `StudentGroup`, `Client`, `StudentRequest`.

Enums in [bot/models/enums.py](bot/models/enums.py):
- `LessonType`: `GROUP` | `INDIVIDUAL`
- `PaymentStatus`: `PENDING` | `PAID`
- `RequestStatus`: `PENDING` | `APPROVED` | `REJECTED`
- `GroupBillingMode`: `NONE` | `PER_VISIT` | `SUBSCRIPTION` (абонемент: фикс `price_full` ₽/мес)
- `StudentGroupTier`: `FULL` | `SHORT` (used only by kindergarten groups ЮБ/БП)

### Utils

- [bot/utils/attendees.py](bot/utils/attendees.py) — parse/serialize `lesson.attendees`. Two CSV formats:
  - Old: `STU-001,STU-002` (no per-student amount → `amount=0` → not billed, shown as «абонемент»)
  - New: `STU-001:60:700,STU-002:60:700` (with `duration_min:amount` snapshot → billed)
  - `parse_attendees()`, `serialize_attendees()`, `attendee_ids()`, `AttendeeEntry` dataclass.
- [bot/utils/bill_format.py](bot/utils/bill_format.py) — `build_bill_text()`. Shared parent-facing bill renderer used by the `admin/bills/` package.
- [bot/utils/dates.py](bot/utils/dates.py) — date helpers (`now_str`, `format_date_display`, `period_month_from_date`, `display_period`, `format_date_short_with_wd`). Formats: storage `YYYY-MM-DD` / `YYYY-MM`, display `ДД.ММ.ГГГГ` / `ММ.ГГГГ`.
- [bot/utils/ids.py](bot/utils/ids.py) — sequential ID generators: `TCH-XXXX`, `STU-XXXX`, `LES-XXXXXX`, `GRP-XXXX`, `BRN-XXXX`, `PAY-XXXXXX`, `SUB-XXXXXX`, `USR-XXXX`, `CLT-XXXX`, `FIN-XXXXXX`. Each scans existing rows for the current max.
- [bot/utils/lesson_stats.py](bot/utils/lesson_stats.py) — `format_lesson_breakdown(lessons) → (group_count, ind_count, group_line, ind_line)` for stats screens.
- [bot/utils/paging.py](bot/utils/paging.py) — `paginate(items, page, size, clamp=False) → Page`; [bot/utils/callbacks.py](bot/utils/callbacks.py) — датаклассы callback (`unpack`/`pack`); [bot/utils/notify.py](bot/utils/notify.py) — `notify()` и `notify_safely(coro, лог)`; `dates.period_label()` — «Сентябрь 2026» (одна реализация для всех экранов).

### Keyboards

Layout in `bot/keyboards/` by role: `admin.py`, `teacher.py`, `client.py`, `common.py`, `calendar.py`. Functions named `kb_*` return `InlineKeyboardMarkup`. **Спецроли-хардкод убраны** (`bot/staff.py` удалён; ранее там жили `PROXY_BUTTONS`/`BILLING_TEACHERS`). Единственное конфигурируемое отличие меню педагога: `kb_teacher_menu` показывает «🧾 Счета моих групп» (`teacher:bills`) педагогам из env `BILLING_TEACHER_IDS`.

`kb_lesson_detail()` takes an `is_admin: bool` flag — when `True`, admins see the delete button even for locked lessons (with `(🔒 период сдан)` suffix). Навигация страниц — `keyboards/common.nav_row(page, callback_for, …)`; `kb_student_paged(Page)`.

### FSM states

In `bot/states/`. Each multi-step flow has its own `StatesGroup`. Some highlights:

- `RecordLessonStates` (~17 states) — the lesson-recording wizard. Handlers live in the [bot/handlers/teacher/record_lesson/](bot/handlers/teacher/record_lesson/) package (P5-split by stage).
- `SubmitPeriodStates` — month picker → confirmation.
- `AddTeacherStates`, `EditTeacherRatesStates`, `AddStudentStates`, `PartnerAssignStates`, `ConfirmPaymentStates`, `StudentListStates` — admin flows.
- `AddBranchStates`, `EditBranchNameStates`, `AddGroupStates`, `EditGroupNameStates`, `GroupBillingStates`, `GroupAddStudentStates` — branch/group flows.
- `TeacherRenameStudentStates`, `TeacherGroupAddStudentStates` — teacher flows.
- `ClientCreateStates`, `ClientRegStates` — client side.
- `AthleteRegStates`, `LogTrainingStates` — спортсмен: регистрация по фамилии, запись тренировки. `GradeEntryStates`, `AssignTaskStates` — педагог: оценка записи, новое задание.
- `ReceiptStates` — uploading a payment receipt (client → admin).

**Admin record-for-teacher pattern**: `proxy_teacher_id` is an internal FSM key set only by the admin handler `admin_rl_tch:*`. Helper `_tid(user, data)` returns `data.get("proxy_teacher_id") or user.teacher_id` (in `record_lesson/_base.py`). The former teacher-to-teacher proxy flow was removed; ordinary teachers cannot set this mode through a registered entry point.

**Lesson-history back-nav**: FSM key `t_stu_les_back` stores the return callback when a teacher opens a lesson detail from a student/pair card. `cb_lesson_detail` in `teacher/my_lessons/detail.py` honors it before falling back to the default.

### Race-condition guards

Module-level `InProgressGuard` lockers (unified in P2, see [bot/utils/locks.py](bot/utils/locks.py) — `key in guard` / `guard.add()` / `guard.discard()`, always released in `finally`) where double-clicks would corrupt data:
- `_confirming_lesson_ids` in `teacher/record_lesson/finalize.py` (per tg_id)
- `_submitting` in `teacher/submit_period.py`
- `_sending_in_progress`, `_confirming_in_progress`, `_group_sending` in `admin/bills/_base.py`
- `_reminding` in `admin/debtors.py`
- `_seen` in `DedupUpdateMiddleware` (raw set + TTL GC)

### ID format

Human-readable sequential strings; never autoincrement integers. See `bot/utils/ids.py`.

---

## Tests

`tests/fakes.py` — фейки Telegram (`FakeCallbackQuery`, `FakeMessage`, `FakeState`, `FakeBot`), in-memory репозитории
(`StudentRepoFake`, `LessonRepoFake`, `PaymentRepoFake`, `StudentGroupRepoFake`, …), конструкторы сущностей (`mk_*`)
и golden-инфраструктура (`assert_golden`, `screen_dump`). `install_fake_show_card()` подменяет `show_card` в модуле
хендлера (настоящий различает `CallbackQuery` по `isinstance`).

Что зафиксировано: экраны (карточки ученика/группы/педагога, занятие, занятия родителя, должники, история оплат,
расшифровка выплаты, счёт родителю, клавиатуры с пагинацией) — golden в `tests/golden/`; формулы денег
(`test_billing_service`, `test_payment_ledger`, `test_debt_map`, `test_subscription_billing`, `test_profit_*`);
инварианты: `test_callback_wiring` (у кнопки есть хендлер), `test_callbacks` (roundtrip строк), `test_guards_static`
(роль у каждого admin/teacher callback), `test_di_wiring` (снимок DI). Открытые дефекты зафиксированы как текущее
поведение в `tests/test_known_bugs.py` (B2) — при исправлении тест переписывается осознанно.

## Billing formulas

```
earned (teacher) = rate × (duration_min / 45)
  rate = rate_group          for group lessons
  rate = rate_for_teacher    for individual lessons

amount (student invoice) = rate_for_student × (duration_min / 45)
  split equally across all students; first student absorbs the integer remainder

amount (PER_VISIT group) = group.price_full or group.price_short
  stored as snapshot in lesson.attendees at creation time: STU-001:60:700

Ставки берутся на дату занятия: `rate_history.effective_rates(teacher, ls.date)` — при повышении цен
  прошлые занятия считаются по строкам листа teacher_rate_history. Граница `until_period` — месяц
  (`YYYY-MM`, по конец месяца) или день (`YYYY-MM-DD`, цена выросла в середине месяца: на проде
  `TCH-0009 | 2026-09-03` — Контарева, повышение для клиента с 4.09.2026). Берётся строка с наименьшей
  границей ≥ даты; если такой нет — ставки из карточки педагога.

earned (REVENUE_SHARE_GROUPS, напр. GRP-0020 «Индивидуальные — Яковлева», 50%) =
  процент × сумма amounts из attendees; длительность не влияет
```

`Billing` rows are virtual — they are computed on demand by `build_billing_rows(lesson, teacher)` from a Lesson + Teacher. No billing repository/sheet is used (the model exists only for shape; `SHEET_BILLING` is legacy configuration).

---

## Payment flow (client side)

**Накопительный счёт (с 2026-09-11).** Строки `StudentPeriodPayment` в листе `student_period_payments` — по связке `(student, teacher_id, period_month)` их может быть **несколько**: N строк `PAID` (каждая — один платёж со своей суммой) и не более одной `PENDING` — **остаток** = начислено − оплачено (может быть 0 — платить нечего). `PaymentService.ledger_for(student, period)` → `{teacher_id: TeacherLedger(accrued, paid, remainder, overpaid, pending, paid_rows, items, subscription)}` ([bot/services/payment_ledger.py](bot/services/payment_ledger.py)) синхронизирует строку-остаток при каждом открытии счёта; `get_or_create_invoices_for_student_period()` возвращает оплаченные строки + остаток. `PaymentRepository.get_by_student_period_teacher()` возвращает **только** строку-остаток, `get_rows_for()` — все. Так родитель может платить после каждого урока: каждое подтверждение закрывает остаток, следующий урок создаёт новый.

**Lessons have no payment status field.** ✅/⬜ считается на лету. Если оплата принята за конкретные занятия (колонка `lesson_ids`, плательщик выбрал их на экране) — галочки стоят именно у них; остальная сумма закрывает уроки по датам, с самых ранних (`lesson_paid_marks(amounts, paid, linked)`), как было раньше. Выбор занятий есть у админа (бот и кабинет), у педагога со счетами и у родителя (экран «🧾 Выбрать занятия» перед способом оплаты: сумма считается по выбранным, а намерение пишется в строку-остаток, поэтому любой способ подтверждения зачтёт именно их). Внизу экрана «Не оплачено: N ₽» и кнопка «💳 Оплатить» (`client_pay:{sid}:{period}[:{teacher_id}]` — при фильтре по педагогу он предвыбран).

**Bill detail** ([my_bills/viewing.py](bot/handlers/client/my_bills/viewing.py) → `parent_views.bill_detail`):
- По педагогу: «Итого X — оплачено» / «оплачено P, к доплате R» / «переплата» (если урок удалили после оплаты — учитывается вручную в следующем месяце).
- "К оплате" = сумма остатков; «Оплатить» скрыта, когда остатков нет. Список месяцев: ✅ всё оплачено, ⏳ есть остаток (подпись «к доплате N», если уже платили).
- «Должники» (`compute_debt_map`) считают долг как начислено − оплачено, поэтому доплата после новых уроков попадает в должников и напоминания.

**Payment methods** — порядок на экране выбора: **наличные → по реквизитам с чеком → СБП онлайн**
(решение владельца 21.09.2026; один и тот же `methods_screen` в боте, MAX и кабинете).
Каждый способ настраивается через ENV — без настройки способа не видно:
1. 💵 Наличные — родитель жмёт «📨 Уведомить об оплате», админ подтверждает кнопкой (`PAYMENT_CASH_ENABLED`, по умолчанию включено; флаг передаётся в `methods_screen(cash=…)` из Telegram- и MAX-хендлеров).
2. 🏦 По реквизитам — shows QR + bank details; client uploads receipt (`PAYMENT_BANK_DETAILS`).
3. 📱 СБП — shows SBP details; client uploads receipt (`PAYMENT_SBP_DETAILS`).
4. 💳 Картой онлайн — YooKassa link (`YOOKASSA_SHOP_ID` + `YOOKASSA_SECRET_KEY`).

Receipt upload uses FSM `ReceiptStates.waiting_for_receipt` ([bot/states/client_states.py](bot/states/client_states.py)). On receipt: admins receive the photo/document with a «✅ Подтвердить оплату» button. Admin confirms → `confirm_period()` → all PENDING invoices for that `(student, period)` → PAID. If `CLOUDKASSIR_PUBLIC_ID` is set and the linked client has a phone, `CloudKassirService.send_income_receipt()` then fires a fiscal receipt for the paid amount.

**YooKassa webhook is verified** ([payments.py: process_yookassa_event](bot/handlers/client/payments.py)): the request body is **not trusted** — only `object.id` is taken from it, then the payment is re-fetched from the YooKassa API (`Payment.find_one`); status/metadata/amount come from the API response. Period is confirmed only on real `succeeded`. Network error during verification → HTTP 500 (YooKassa retries). Tests: [tests/test_yookassa_webhook.py](tests/test_yookassa_webhook.py).

**Important**: занятие, добавленное после оплаты, **доначисляется** автоматически (новая строка-остаток по педагогу); оплаченные строки никогда не меняются. Удаление оплаченного урока даёт «переплату» — она показывается, но не переносится автоматически.

---

## Key domain rules

- **Period submission**: once submitted, the teacher can no longer edit/delete lessons in that period.
  - **Admins (and `bypass_period_lock=True` callers) can both create AND delete** lessons in a submitted period — both `LessonService.create()` and `LessonService.delete()` accept the flag.
  - `kb_lesson_detail` renders «🗑 Удалить занятие» for admins even when locked (via `is_admin=True`).
  - Teachers can submit a period only from the **25th of the month**.
  - Admins reopen a whole period via teacher card → «🔓 Открыть период» (deletes the submission row entirely).
- **Admin bills bypass submission**: admins can issue/send a bill to a parent at any moment — no «period not submitted» blocker. Bill amounts are recomputed from current lessons on each open.
- **ИНВАРИАНТ оплат: оплачено клиентом ≥ отмечено педагогом** (индивидуальные и групповые). Обеспечивается сдачей периода (замок на добавление занятий → счёт финален к оплате). Правка админом **оплаченного** периода нарушает инвариант (доначисления нет) — по регламенту в оплаченный период изменения не вносить.
- **Lesson names are denormalized**: `lesson.teacher_name`, `student_N_name` are creation-time snapshots. Renames don't rewrite history.
- **Teacher visibility**: derived from `teacher_groups` ∩ `student_groups`. There is **no** `teacher_students` table.
- **Multi-group students**: a student can belong to multiple groups; billing aggregates across all per period.
- **SHORT/FULL tiers** (`StudentGroupTier`): only for kindergarten groups (ЮБ/БП); all others have one price.
- **Group billing modes**: `NONE` (free, attendance not billed), `PER_VISIT` (each attended lesson billed at group price), `SUBSCRIPTION` (абонемент: фиксированная `price_full` ₽/мес с каждого ученика группы, **независимо от числа занятий**; начисляется **каждый месяц с первого занятия группы, каникулы тоже, кроме июля/августа** (летом — только за месяц с ≥1 занятием; `subscription_billable_months`, правило 2026-09-08); ключ начисления в счетах/долгах — `SUB:{group_id}`, подпись в счёте — просто «Абонемент» (название группы не влезало); см. `PaymentService._subscription_bills_for_student`). Начисляется только за месяцы членства (`joined_period` ≤ месяц < `left_period`) — учитывается в счетах, `compute_debt_map` и `subscription_revenue_breakdown`. Выход из абонементной группы не удаляет строку, а ставит `left_period` ([bot/services/membership.py](bot/services/membership.py): `leave_group`, `leave_options` — «с этого месяца» / «со следующего»); во всех трёх местах удаления (админ: карточка ученика и карточка группы, педагог: своя группа) показывается выбор месяца. UI правок — «📅 Месяцы членства» в карточке абонементной группы, callbacks `group_joined:` / `gjset:` / `gjdo:` / `gldo:`. Цена переопределяется на конкретный месяц для ученика или всей группы (лист `subscription_overrides`, приоритет: ученик/месяц → постоянное правило ученика (`period_month="*"`) → группа/месяц → `price_full`; `0` = не начислять; UI — «Биллинг ученикам» группы, callbacks `subovr:*`). Смена базовой цены действует **только вперёд**: админ выбирает месяц начала действия (`subeff:`), прошлые активные месяцы автоматически фиксируются старой ценой (или `0` при первом включении) — `PaymentService.pin_subscription_history`.
- **Архив группы**: карточка группы → «📦 В архив» (`group_arch:on|off:{gid}`). Архивная группа пропадает из выбора при записи занятия, выставлении счетов, добавлении учеников и из карточки педагога; в карточке филиала она внизу с 📦, в истории оплат и выплат — видна. Строка не удаляется, поэтому прошлые занятия, счета и зарплаты считаются как прежде. Начисления (`payment_service`) и удаление филиала читают группы с `include_archived=True`.
- **NONE/SUBSCRIPTION groups auto-save**: recording a group lesson for these modes skips attendance and saves immediately with `attendees=None` (roster shown only for PER_VISIT).
- **Duplicate guard**:
  - Group lessons: **not** blocked — a group can be recorded twice in one day (different shifts/streams).
  - Solo lessons (exactly 1 student): blocked by `teacher + student + date` via `individual_lesson_exists`. Pairs are intentionally not blocked (a student can be in a pair and a solo on the same day).
- **Client vs Student**: a student attends lessons; the client (parent) pays. Separate entities: `student.client_id → Client`; `student.parent_tg_ids → list[int]` of Telegram IDs with bot access.
- **parent_tg_ids separator**: uses `|` (pipe), **not** `,` (comma). Russian-locale Google Sheets corrupts comma-joined integers (see Repositories section). Parser accepts both for backwards compatibility (`student_repo.py`).
- **Client registration**: first-time registration is direct (no approval). Adding a second student requires admin approval via `client:add_child` FSM → `admin_child_ok` / `admin_child_no` callbacks ([admin/client_requests.py](bot/handlers/admin/client_requests.py)).

---

## Full feature inventory

### Admin

| Feature | Entry point | Notes |
|---|---|---|
| Teacher list | `teachers:list` | Shows 🟢/🔴 submission status for the previous month |
| Teacher card | `teacher_card:{id}` | Rates, groups, submission history |
| Add teacher | `teachers:add` | FSM: tg_id → name → 3 rates |
| Edit rates | `card_edit_rates:{id}` | FSM: pick rate type → enter value |
| Edit groups | `t_edit_groups:{id}` | Checkbox list, confirm/cancel |
| Delete teacher | `del_teacher:{id}` | Confirmation screen; also cleans `teacher_groups` |
| **Open submitted period** | `open_period_list:{id}` | Lists submitted periods; deletes chosen submission row → teacher can edit again |
| Student list | `students:list` | Search by name; paged (PAGE_SIZE=20) |
| Student card | `student_card:{id}` | Partner, group(s), client link |
| Add student | `students:add` | Name only; groups/partner assigned later |
| Rename student | `student_rename:{id}` | |
| Delete student | `student_delete:{id}` | Clears partner link first |
| Manage partner | `student_set_partner:{id}` / `student_clear_partner:{id}` | Symmetric: both rows updated, rolled back on failure |
| Link client | `student_link_client:{id}` | Associates student with a Client entity |
| Approve child request | `admin_child_ok:{tg_id}:{student_id}` | Sent by a parent via `client:add_child` |
| Salaries | `admin:salaries` | Earnings per teacher per period |
| **Payouts** | `admin:payouts` | «💸 Выплатить зарплату»: месяц → педагоги (🟢/🟡/🔴, выплачено/начислено) → «✅ Выплатить остаток» или произвольная сумма (FSM `PayoutStates`). Начислено = `calc_earned` по занятиям; выплачено = сумма строк `teacher_payouts` ([admin/payouts.py](bot/handlers/admin/payouts.py)) |
| **Отметить оплату по занятиям** | `paysel:{payment_id}:{group_id}` | «💾 Подтвердить оплату» → ученик → педагог: экран занятий с ⬜/☑️/✅ (оплаченные — `noop`), `pslt:{i}` переключает, `pslgo` → подтверждение, `pslok:{сумма}` зачитывает через `PaymentService.record_payment` (остаток закрывается от ранних занятий). Выбор хранится в FSM-ключах `psel_*`; абонемент отмечается только целиком (`pay_invoice:`). Данные — `PaymentService.teacher_lesson_marks` / `payment_ledger.lesson_marks` |
| **Payment history** | `admin:payhist` | «📜 История оплат»: фамилия ученика (FSM `PaymentHistoryStates`) → месяцы (оплачено / ожидает) → оплаты месяца: педагог/абонемент, сумма, дата, способ (ЮКасса при `confirmed_by_tg_id=0`, иначе вручную) ([admin/payment_history.py](bot/handlers/admin/payment_history.py)) |
| Bills | `admin:bills` | Период → филиал → группа → **[📨 вся группа]** или ученик → отправка родителю (multi-recipient, no submission check). Рассылка по группе — `bill_group_send:{period}:{gid}` |
| **Debtors** | `admin:debtors` | Сводный долг по всем ученикам/периодам (on-demand: начисления − PAID). Текущий месяц помечен `*` и в напоминание не входит. «📤 Напомнить всем» — рассылка родителям должников за закрытые месяцы (с подтверждением) |
| Прибыль | `admin:profit` | Месяц: занятия + абонементы + ручные доходы (турниры) − зарплата − расходы (аренда); ввод/удаление записей кнопками ➕/🗑 (fin:*). День: только занятия |
| Branches/Groups | `admin:branches` | CRUD branches, groups, billing modes, prices; bulk bill send per group |
| Edit lessons | `admin:edit_lesson` | Pick teacher → date/month/all → view+delete (bypasses period lock) |
| Diagnostics | `admin:diagnostics` | Cache and data health checks |
| **Очередь решений** | кабинет: «📥 Ждут решения» + кнопки `pact:`/`pnay:` в уведомлениях | Чеки, наличные и заявки родителей из листа `pending_actions` + заявки педагогов из `student_requests`; решение здесь зачитывает оплату (`record_payment`) или привязывает родителя и закрывает строку, поэтому кнопки в чате и кабинет не расходятся ([bot/api/admin_inbox.py](bot/api/admin_inbox.py)) |
| Record for teacher | `admin:record_lesson` | Proxy: pick teacher → full lesson FSM (bypasses period lock) |

### Teacher

| Feature | Entry point | Notes |
|---|---|---|
| Record lesson | `teacher:record_lesson` | FSM: type → students/group → date → duration → confirm |
| View lessons | `teacher:lesson_view` | Filter: today / yesterday / date / month; type toggle; paged |
| Delete lesson | `teacher:lesson_delete` | Same filters; locked lessons hidden for teacher, visible for admin |
| **Submit period** | `teacher:submit_period` | Locked: allowed only from 25th of month; shows summary before confirm |
| My groups | `teacher:my_groups` | Group roster; click student → student card |
| My pairs | `teacher:my_pairs` | Grouped by group; click pair → pair card |
| My soloists | `teacher:my_soloists` | Students without partner |
| Student card | `t_student_card:{id}` | Name, partner info; rename button |
| **Lessons by student** | `t_stu_les:{id}` → `t_stu_les_m:{id}:{ym}` | From student card: all teacher's lessons with this student; month picker |
| Pair card | `t_pair_card:{id}` | Partner info; change/remove partner buttons |
| **Lessons of pair** | `t_pair_les:{id}:{partner_id}` → `t_pair_les_m:{...}:{ym}` | From pair card: lessons where BOTH students appear; month picker |
| Assign partner | `t_partner_assign:{id}` | Picks from visible students |
| Remove partner | `t_partner_clear:{id}` | Confirmation screen |
| Rename student | `t_rename_student:{id}` | FSM: enter name |
| My stats | `teacher:my_stats` | Personal earnings summary |
| **Bills for own groups** | `teacher:bills` | Только педагоги из `BILLING_TEACHER_IDS`. Период → своя группа → ученик/вся группа → счёт родителю ([teacher/bills.py](bot/handlers/teacher/bills.py), переиспользует helpers и локи `admin/bills/`). Счёт полный — по всем педагогам ученика. |

### Client (parent)

| Feature | Entry point | Notes |
|---|---|---|
| Registration | `/start` (new user) | Direct for first student; admin approval for second |
| Add child | `client:add_child` | FSM: search student name → send approval request to admin |
| My lessons | `client:lessons` | Select student or "Все вместе"; date/month filter; ✅ on paid lessons |
| My bills | `client:my_bills` | Select student; list of months with status icon |
| Bill detail | `client_bill:{stu}:{month}` | Per-teacher breakdown; paid ✅; "К оплате" = unpaid total only |
| Pay — method select | `client_pay:{stu}:{month}` | Shows only configured payment methods |
| Pay — card online | `pay_method:yookassa:{...}` | Opens YooKassa link |
| Pay — bank transfer | `pay_method:bank:{...}` | Shows QR + bank details; prompts receipt upload |
| Pay — cash | `pay_method:cash:{...}` | "Notify admin" button |
| Pay — SBP | `pay_method:sbp:{...}` | Shows SBP details; prompts receipt upload |
| Upload receipt | `receipt_upload:{method}:{...}` | FSM `ReceiptStates.waiting_for_receipt`; forwards to admins |
| Admin confirms payment | `receipt_confirm:{stu}:{month}` | → `confirm_period()` → all PENDING → PAID → CloudKassir fiscal receipt (if configured) |
| Cash notify | `cash_notify:{stu}:{month}` | Sends admin notification with confirm button |
| Training diary (read-only) | `client:diary` → `cldiary:stu:{id}` / `cldiary:m:{id}:{YYYY-MM}` | Дневник ребёнка: статистика месяца, место в рейтинге, записи с оценками и комментариями педагога, открытые задания ([client/diary.py](bot/handlers/client/diary.py)) |

### Athlete (спортсмен)

Ученик спортивной группы (`ATHLETE_GROUP_IDS`) со своим Telegram. Пакет [bot/handlers/athlete/](bot/handlers/athlete/), клавиатуры [bot/keyboards/athlete.py](bot/keyboards/athlete.py). Роль хранится в `students.athlete_tg_id` (колонка 9); резолв — `show_family_menu()` в [common.py](bot/handlers/common.py) (спортсмен + родитель одновременно → выбор кабинета `mode:athlete` / `mode:client`).

| Feature | Entry point | Notes |
|---|---|---|
| Registration | `/start` → «🏃 Я спортсмен» (`athreg:athlete`) | Фамилия → поиск только среди учеников `ATHLETE_GROUP_IDS` → «Да, это я» → привязка **без одобрения**; админам уведомление с «🚫 Отвязать» (`athreg:unlink:{sid}:{tg}`), родителям — информационное |
| Log training | `ath:log` | FSM `LogTrainingStates`: дата (сегодня/вчера/календарь `athcal_*`) → минуты → темы (мультивыбор по индексу `athlog:tp:{i}`) → открытые задания (`athlog:tk:{id}`, шаг пропускается, если заданий нет) → комментарий |
| My entries | `ath:entries` → `athent:list:{YYYY-MM}` / `athent:view:{id}` | Месяц (этот/прошлый); удалить можно только запись без оценки (`athent:del:{id}`) |
| My tasks | `ath:tasks` | Открытые задания с «сделано N раз, последний DD.MM» |
| Stats | `ath:stats:{YYYY-MM}` | Тренировок, минут, по танцам, средняя оценка, очки, место |
| Rating | `ath:rating:{YYYY-MM}:{topic_idx\|all}` | Топ-10 + своё место; фильтр по танцу (индекс в `ALL_TOPICS`) |

**Teacher / admin side** — пакет [bot/handlers/teacher/diary/](bot/handlers/teacher/diary/) (`teacher:diary` / `admin:diary`): список спортсменов с «🆕 N без оценки» → дневник (`tdiary:stu:{sid}:{YYYY-MM}`) → запись (`tdiary:entry:{id}`) → «⭐ Оценить» (`tgrade:*`, 1–5 + комментарий; push спортсмену и родителям) · «➕ Задание» (`ttask:new:{sid}`: упражнение из недавних своих или текст → минуты → комментарий; push спортсмену) · «📋 Задания» (`ttask:list:{sid}`, `ttask:close:{id}`) · «🏆 Рейтинг» (`tdiary:rating:*`, полный список). Педагог видит спортсменов своих групп (`TeacherVisibilityService`), админ — всех; педагогу push не шлём. Кнопка «📓 Дневник спортсмена» есть и в карточке ученика педагога.


---

## Special roles & configuration

**Спецроли-хардкод убраны** (конфиг в `bot/staff.py`, ныне удалён). Право счетов вернулось в конфигурируемом виде: env `BILLING_TEACHER_IDS` (на проде — `TCH-0009` Контарева) даёт педагогу «🧾 Счета моих групп», ограниченные его `teacher_groups`. Ранее было две спецроли:
- **Клецова (TCH-0002)** — прокси-запись за Никишина/Криворчук. Удалено (прокси-хендлеры `proxy_*` вырезаны из `record_lesson/entry.py`; попутно закрыт баг B1). Запись за педагога осталась **только у админа** — «📝 Отметить занятие» → выбор педагога (`admin:record_lesson` → `admin_rl_tch:`, независимый флоу).
- **Контарева (TCH-0009)** — личный биллинг по своим группам. Убран; выставление счетов — общая админская фича «🧾 Счёт ученика за период» ([admin/bills/](bot/handlers/admin/bills/)): на экране группы кнопка «📨 Отправить счета всей группе» (`bill_group_send:`) либо выбор конкретного ученика.

**PER_VISIT groups** (each attended lesson billed):
- GRP-0007, GRP-0008, GRP-0010, GRP-0015 — various groups
- GRP-0017 «БП БТ Спортивная — Никишин» (TCH-0005): `price_full=800` (с 09.2026; до этого 700)
- GRP-0018 «БП БТ Спортивная — Криворчук» (TCH-0008): `price_full=800` (с 09.2026; до этого 700)

---

## Scripts & operations

`scripts/` contains both deployment helpers and one-off maintenance scripts:

| File | Purpose |
|---|---|
| `deploy.sh` | Production deploy — rsync working tree to `root@178.104.240.252:/opt/fokus-bot/` (excludes `.git`, `.venv`, `.env`, credentials, `bot.log`, `__pycache__`), then `systemctl restart fokus-bot && journalctl -n 20`. Standard deploy command for this repo. |
| `audit_teacher_students.py` | Read-only consistency check: every student's visibility to each teacher matches `teacher_groups ∩ student_groups`. |
| `migrate_student_groups.py` | One-shot migration from legacy `students.group_id` column to the `student_groups` join table. Idempotent. |
| `bulk_seed_2026_04.py` | One-shot seeding of students + group assignments for a specific intake (April 2026). Has `--dry-run` and `--apply` flags. |
| `setup_group_archive.py [--apply]` | Идемпотентно добавляет колонку `groups.archived` (архив групп). |
| `setup_joined_period.py [--apply]` | Идемпотентно добавляет `student_groups.joined_period` и `left_period`, проставляет существующим строкам первый месяц занятий их группы (поведение счётов не меняется). |
| `setup_pending_actions.py` | Идемпотентно создаёт лист `pending_actions` (очередь решений администратора). |
| `setup_payment_lessons.py [--apply]` | Идемпотентно добавляет колонку `student_period_payments.lesson_ids` (занятия, за которые принята оплата). |
| `setup_diary_sheets.py` | Идемпотентно создаёт листы `training_entries`, `athlete_tasks` и колонку `students.athlete_tg_id` (9-я) для кабинета спортсмена. |
| `send_bills_grp0004.py` | Template script for ad-hoc bill mailings to one group. Parametrized at the top (`GROUP_ID`, `PERIOD`, `RECIPIENT`). |

> Синхронизация посещений Яковлевой (ХГ) из внешней Google-таблицы (скрипт `sync_yakovleva_attendance.py` и таймер
> `fokus-sync-yakovleva.timer`) **удалена 19.09.2026 по решению владельца**: занятия и составы ХГ ведутся вручную в боте,
> как у остальных педагогов. Внешняя таблица — только справочно, автоматически ничего не переносится.

Production: **Hetzner VPS (Nuremberg)**, systemd unit `fokus-bot.service`, deployed by `./scripts/deploy.sh`. Logs via `journalctl -u fokus-bot`. The Railway-related `WEBHOOK_URL` / `Procfile` are present but unused — current production runs in polling mode under systemd.

---

## Environment

Required:
- `BOT_TOKEN` — Telegram bot token
- `GOOGLE_CREDENTIALS_JSON` — service account JSON (inline, single-line)
- `SPREADSHEET_ID` — main Google Spreadsheet ID

Optional — Google Sheets tab names (have sensible defaults — only set to override): `SHEET_USERS`, `SHEET_TEACHERS`, `SHEET_STUDENTS`, `SHEET_LESSONS`, `SHEET_BILLING`, `SHEET_PAYMENTS`, `SHEET_TEACHER_PERIOD_SUBMISSIONS`, `SHEET_BRANCHES`, `SHEET_GROUPS`, `SHEET_TEACHER_GROUPS`, `SHEET_STUDENT_GROUPS`, `SHEET_STUDENT_REQUESTS`, `SHEET_CLIENTS`, `SHEET_SUBSCRIPTION_OVERRIDES`, `SHEET_FINANCE_ENTRIES`, `SHEET_TRAINING_ENTRIES`, `SHEET_ATHLETE_TASKS` (два последних + колонка `students.athlete_tg_id` создаются скриптом `scripts/setup_diary_sheets.py`).

Optional — payments:
- `DEBTORS_SINCE_PERIOD` — долги на экране «⚠️ Должники» считаются с этого периода (`YYYY-MM`); пусто — за всё время. Отсекает месяцы до внедрения учёта оплат (на проде: `2026-07`).
- `PAYMENT_CASH_ENABLED` — show cash payment option (default `True`)
- `PARENT_RECEIPT_EMAIL` — показывать родителю email для фискальных чеков: кнопка «✉️ Email для чеков» в меню (`menu_rows(receipt_email=…)`) и шаг после телефона при регистрации по ссылке группы (`group_link._ask_email`). `false` — кнопки нет, шаг пропускается, старая кнопка отвечает «Раздел временно недоступен»; сохранённые email и отправка `receipt` в ЮКассу не меняются (на проде `false` с 13.09.2026, пока решается вопрос с фискализацией).
- `PAYMENT_BANK_DETAILS` — bank details text (`\n` becomes a newline; handler replaces `\\n` → `\n`)
- `PAYMENT_QR_DATA` — ЦБ РФ format string for QR generation (`ST00012|Name=...|PersonalAcc=...`)
- `PAYMENT_QR_IMAGE_URL` — fallback public HTTPS URL for QR image
- `PAYMENT_SBP_DETAILS` — SBP details text (phone / link)
- `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — YooKassa credentials
- `YOOKASSA_RETURN_URL` — return URL after YooKassa payment (default `https://t.me/fokus_bot`)
- `PAYMENT_WEBHOOK_PORT` — port for the YooKassa webhook aiohttp server (default `8081`)
- `CLOUDKASSIR_PUBLIC_ID`, `CLOUDKASSIR_API_SECRET` — fiscal receipt service; if empty, fiscal receipts are skipped silently
- `BILLING_TEACHER_IDS` — teacher_id через запятую/`|` (напр. `TCH-0009`): этим педагогам доступна кнопка «🧾 Счета моих групп» — просмотр и отправка счетов родителям учеников **своих** групп. Пусто — счета только у админов.
- `DIRECT_PAY_TEACHER_IDS` — teacher_id через запятую (на проде: `TCH-0002` Клецова): **индивидуальные** занятия этих педагогов родители оплачивают педагогу напрямую — `build_billing_rows` не начисляет их (счета/долги/прибыль), `calc_earned` = 0. В счёте родителя такой педагог показан как все — раздел «Оплачивается педагогу напрямую» с занятиями и суммами (`build_billing_rows(..., include_direct=True)`, ключ строки `DIRECT:{tid}`, `rest=0`, галочки выбора нет), с пометкой «в сумму „К оплате“ не входят, школа их не отслеживает»; в «К оплате», начисления школы и долги они не идут. Группы педагога — как обычно.
- `OWNER_TEACHER_IDS` — teacher_id через запятую (на проде: `TCH-0001` Река): руководитель школы, ведущий занятия. В «Прибыли» его зарплата не вычитается (`salary = 0`, `TeacherProfitRow.owner_income` / `ProfitSummary.owner_income` показываются строкой «👑 руководитель … в прибыли»); «Зарплаты» и «Выплаты» не меняются.
- `HALL_RENT_PER_LESSON` — `TCH-XXXX:сумма,...` (на проде: `TCH-0002:500`): педагог с прямой оплатой перечисляет школе фикс. аренду зала с каждого своего **индивидуального** занятия — любой длительности и числа учеников. Считается только в «Прибыли» (`profit_service.lesson_rent`): выручка = аренда, зарплата = 0; в строке педагога и в итогах выводится «в т.ч. аренда зала». Счетов родителям и долгов не создаёт. Работает только вместе с `DIRECT_PAY_TEACHER_IDS`. `HALL_RENT_SINCE_PERIOD` (на проде `2026-08`) — считать аренду только с этого месяца; пусто — за всё время.
- `GROUP_SALARY_RATES` — `GRP-XXXX:ставка,...` (₽ за 45 мин, как `rate_group`): занятия этой группы оплачиваются педагогу по ставке группы, а не по карточке педагога. На проде: `GRP-0019:1500` — «БП Джаз» Клецовой, занятие 60 мин = 2000 ₽ (решение владельца 19.09.2026). Применяется в `calc_earned`, поэтому видно в «Зарплатах», «Выплатах» и «Прибыли».
- `REVENUE_SHARE_GROUPS` — `GRP-XXXX:процент,...`: в этих группах зарплата педагога = процент от сбора с посетивших (а не ставка × время). На проде: `GRP-0020:50` — «ХГ Индивидуальные — Яковлева» (1–3 ученицы по 1800 ₽, техгруппа скрыта из UI педагога, шаг длительности пропущен).
- `SHIFT_GROUPS` — `GRP-XXXX:start-end,...` (минуты от начала смены): группы одной смены внахлёст. Зарплата за день = `rate_group` × длина объединения интервалов групп, у которых в этот день были занятия (`bot/services/salary_service.py`: `shift_minutes`, `compute_salary_lines`); занятия этих групп per-lesson дают 0. Нестандартные дни — лист `salary_day_overrides` (минуты за дату заменяют расчёт; UI: «💸 Выплатить зарплату» → педагог → «🕒 Нестандартный день»). На проде: Боброво ХГ Яковлевой `GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180`, `SHIFT_LABEL=Смена Боброво` (все три → 3 ч = 5100 ₽). Заменяет прежний костыль `SALARY_DURATION_GROUPS` для этих групп.
- `CASH_PREFERRED_GROUP_IDS` — группы, где школа предпочитает наличные (по умолчанию `GRP-0001` БП БТ Спортивная, `GRP-0004` БП БТ Детская спортивная, `GRP-0002` ВБ БТ Старшая): в кабинете родителя «💵 Наличные» идут первой кнопкой и подписаны «в этой группе удобнее наличными»; остальные способы остаются. Флаг отдаётся в `/api/parent/me` по каждому ребёнку (`cashPreferred`).
- `CASH_DISABLED_GROUP_IDS` — группы, где наличные **не принимаются**: способ не показывается ни в кабинете, ни в боте (пусто — принимаются везде, где включён `PAYMENT_CASH_ENABLED`). Флаг по ребёнку — `cashAllowed` в `/api/parent/me`; общая точка расчёта — `parent_views.cash_options(student_id, student_group_repo)`.
- `ATHLETE_GROUP_IDS` — спортивные группы (по умолчанию `GRP-0001` «БП БТ Спортивная»): их ученики могут завести кабинет спортсмена — сами находят себя по фамилии на `/start` → «Я спортсмен». Участники рейтинга — все привязанные спортсмены.
- `SALARY_DURATION_GROUPS` — `GRP-XXXX:минуты,...`: зарплатная длительность группы независимо от выбранной при записи (пересекающиеся по времени группы). `0` = занятие отмечается (абонемент срабатывает), но в зарплату не идёт. На проде: `GRP-0021:0,GRP-0022:90,GRP-0023:90` — вечер ВБ ХГ Яковлевой 17:00–20:00 = Младшая 0 + Средняя 1,5 ч + Старшая 1,5 ч = 6000 ₽.

Optional — MAX (кабинет родителя):
- `MAX_BOT_TOKEN` — токен бота MAX от @MasterBot; пусто — MAX не запускается. Ссылки групп для MAX используют тот же `GROUP_LINK_SECRET`.

Optional — Mini App:
- `MINIAPP_DEV_TG_ID` — tg_id, под которым API кабинета принимает заголовок `Authorization: dev` (только для локальной разработки; на проде пусто).
- `MINIAPP_URL` — публичный HTTPS-адрес страницы кабинета (на проде `https://fokus.178-104-240-252.sslip.io/app/`, HTTPS через Caddy в `/opt/n8n/Caddyfile`); задан → `scripts/set_miniapp_menu.py` ставит кнопку меню «Кабинет» в чатах (по умолчанию всем: админам, педагогам, родителям, спортсменам; `--staff` / `--admins` — уже. Отдельной кнопки в меню бота нет: кабинет открывается только кнопкой меню чата).

Optional — infrastructure:
- `WEBHOOK_URL` — if set, bot runs in webhook mode at `/webhook/{bot_token}` (currently unused in production)
- `PORT` — HTTP port (default `8080`; Railway sets automatically)
- `REDIS_URL` — FSM state persistence across restarts (`MemoryStorage` if absent)

A `.env.example` and `Procfile` (`worker: python -m bot`) are at the repo root for Railway/dev convenience; current production does not use Railway.
