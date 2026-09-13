# План поведение-сохраняющего рефакторинга (этап A — аудит)

Дата аудита: 2026-09-13. Ветка `refactor/foundation`, HEAD `7081329`. Тесты: 254 зелёных.
Предыдущие этапы (P2 — локеры, P5 — разбивка god-файлов на пакеты) не переделываются.

Цифры ниже получены статическим анализом `bot/` (grep/скан AST по отступам), цитируются
как «файл:строка» на момент аудита.

---

## 0. Что уже хорошо (не трогаем)

- Слои `handlers → services → repositories → models` в целом соблюдены: **gspread нигде, кроме
  `bot/repositories/`** (в `scripts/` — намеренно, это утилиты вне бота); приватные методы
  `BaseRepository` (`_all_records`, `_find_row_index`, …) снаружи репозиториев не вызываются.
- Деньги живут в сервисах: `billing_service` (чистые функции), `payment_service`,
  `payment_ledger`, `salary_service`, `profit_service` — и покрыты тестами (см. §4).
- Гарды доступа уже сведены в один модуль [bot/handlers/access.py](../bot/handlers/access.py)
  (`is_admin`, `is_teacher`, `is_teacher_or_admin`, `can_teacher_bill`); дублируется только
  *вызов*, а не логика.
- Карточка ученика админа уже рендерится из DTO `StudentCard` (`StudentService.get_student_card`).
- Экран занятия (`lesson_detail:`) один на педагога и админа.
- `tests/test_callback_wiring.py` статически проверяет, что у каждого литерального
  `callback_data` есть хендлер; `tests/test_max_front.py` — то же для MAX.

---

## 1. Карта дублирования

### 1.1 Гарды доступа (одинаковый блок из 3 строк)

| Шаблон | Вхождений | Файлов |
|---|---|---|
| `if not _is_admin(user): await callback.answer("Нет доступа", show_alert=True); return` | **137** | 32 (`admin/*`) |
| `if not _is_teacher(user)` / `is_teacher_or_admin(user)` + тот же alert | **≈66** | 20 (`teacher/*`) |
| Родитель: `students = await student_repo.get_by_parent_tg_id(tg_id); if not students: …` | 22 | 6 (`client/*`, `max/*`) |
| Спортсмен: `diary_service.athlete_by_tg(...)` + alert | 4 | 2 |

Топ файлов: `admin/payouts.py` (12), `admin/edit_lesson.py` (12), `admin/bills/confirm.py` (11),
`admin/branches/groups.py` (10), `admin/salaries.py` (9). Особенности, которые нельзя потерять:
- в `common.py` 11 проверок — это **не гарды**, а ветвление меню по роли (`mode:admin/teacher`);
- `record_lesson/*` использует admin-inclusive `is_teacher_or_admin` (см. докстринг `access.py`);
- `teacher/my_lessons/_base.py::_can_view_lesson` и `my_lessons/guests.py:98` — гарды по
  *владению* (занятие своего педагога, сданный период), а не по роли — остаются в хендлере;
- гард родителя одновременно **загружает данные** (`students`) — это не роль-фильтр, а
  выборка; заменяется на фильтр, который кладёт `students` в `data` (ownership-filter).

### 1.2 Ручной разбор callback-строк

`callback.data.split(":")` / `payload.split(":")` — **254 вхождения в 69 файлах**;
`aiogram.filters.callback_data.CallbackData` не используется нигде. Топ: `admin/edit_lesson.py` (11),
`admin/branches/groups.py` (10), `client/my_bills/payment.py` (9), `admin/bills/confirm.py` (9),
`client/my_lessons.py` (8), `admin/salaries.py` (8).

Что мешает механически перейти на `CallbackData`:
- **опциональные хвосты**: `client_pay:{sid}:{period}[:{teacher_id}]`, `rcpp:…:{sel_pids}[:{amount}]:{code}`,
  `receipt_confirm:{sid}:{period}[:{amount}]:{code}` — `CallbackData.pack()` всегда пишет все поля
  (`prefix:a:b:` вместо `prefix:a:b`) → строка изменится, старые кнопки в чатах перестанут матчиться;
- в значениях встречается `:` и `.` (списки `pid` через `.`, фильтр `m-2026-09`) — `split(":", n)`
  с разным `maxsplit` в разных местах;
- лимит 64 байта: часть callback уже на грани (`rcpp:STU-0000:2026-09:123.456:12000:b`).

Поэтому цель 3.1 реализуется как **`bot/utils/callbacks.py` с явными `pack`/`unpack` на
именованные датаклассы**, воспроизводящими текущие строки байт-в-байт (roundtrip-тест по
корпусу всех f-строк `callback_data=` из исходников), а `CallbackData` — только для новых кнопок.

### 1.3 Пагинация — 4 несовместимые реализации

| Где | Размер | Callback | Навигация |
|---|---|---|---|
| `admin/students/listing.py::_filter_and_page` + `keyboards/admin.py::kb_student_paged` | `_STUDENT_PAGE_SIZE=20` (в keyboards!) | `spage:{n}` | «← Пред.» / «След. →» |
| `keyboards/teacher.py::kb_lesson_list` (педагог + `admin/edit_lesson.py`) | `PAGE_SIZE=20` (`utils/constants`) | `lessons_page:{n}:{filter}` / `aedl_…` | « / » + фильтры |
| `admin/debtors.py::_render_debtors` + `_kb_debtors` | `_PAGE_SIZE=25` | `debtors:p:{n}` | « / `1/3` / » |
| `admin/teachers/listing.py`, `payment_history.py` | без пагинации (весь список) | — | — |

Общее ядро — `chunk = items[page*size:(page+1)*size]`, `pages = ceil(len/size)`, clamp, nav-ряд.
Различаются подписи и наличие счётчика → выносится **`bot/utils/paging.py`** (`paginate()` +
`nav_row(prefix, page, pages, style)`), подписи остаются параметрами.

### 1.4 Рендер карточек — копии и «почти копии»

| Сущность | Админ | Педагог | Разница |
|---|---|---|---|
| Ученик | `admin/students/_base.py::_render_student_card` (из `StudentCard`) | `teacher/partners/cards.py::_render_student_card` (читает репо сам) | тексты разные (у педагога нет групп/тарифа/клиента), общие блоки: заголовок, «Партнёр: — (солист)» / «(удалён: …)» |
| Группа | `admin/branches/_base.py::_render_group_card` | `teacher/my_groups/_base.py::_render_t_group_card` | тексты разные; общий блок — состав (`member_ids` ∩ `student_repo.get_all()` + сортировка) |
| Занятие | `teacher/my_lessons/detail.py` (общая) | — | уже одна |
| Педагог | `admin/teachers/groups.py::_render_teacher_card` | — | одна |

Повторяющийся фрагмент «состав группы» (`member_ids = set(await student_group_repo.get_students_for_group(gid)); [s for s in await student_repo.get_all() if s.student_id in member_ids]; sort`)
встречается в **≥ 7 местах**: `admin/branches/_base.py:66`, `teacher/my_groups/_base.py:59`,
`admin/payment_history.py:107`, `max/handlers/start.py:40`, `student_service.pairs_in_group`,
`student_service.soloists_in_group`, `diary_service.registration_candidates`. ⚠️ ключ сортировки
различается (`s.name` vs `s.name.lower()`) — при выносе он должен стать параметром.

Повторяющийся фрагмент «имя филиала группы» (`branch = await branch_repo.get_by_id(g.branch_id); branch.name if branch else "—"/g.branch_id`) —
`student_service.py:105/224`, `admin/students/groups.py:135`, `admin/branches/_base.py`,
`teacher/my_groups/_base.py`, `max/handlers/start.py:38`, `student_service.create_with_group`.
⚠️ fallback различается («—» vs `branch_id`) — сохранять по месту.

Цель 3.4 = «один источник» **не как один текст**, а как: DTO из сервиса + чистые билдеры текста
в `bot/screens/` (как уже сделано для родителя: `screens/parent_bills.py` + `services/parent_views.py`),
где вариант «админ/педагог» — параметр; хендлер только `show_card(text, kb)`.

### 1.5 Прочее

- `show_card` (delete + answer) и `_send_or_edit` в `common.py` — уже общие. Ещё 30
  `except Exception` в хендлерах: 18 — вокруг «уведомить педагога/админа/родителя»
  (нужен один `notify_safely()`), остальные — вокруг записи в Sheets с текстом «Ошибка при …».
- `teacher/bills.py` импортирует локеры и helpers из `admin/bills/*` (кросс-ролевой импорт;
  правильнее — общий модуль `bot/handlers/shared/bills.py`, но это только перенос).
- `bot/handlers/teacher/my_lessons/_base.py::_month_label` дублирует `parent_views.period_label`
  и `utils/dates.display_period`-семейство (три функции «месяц словами»).

---

## 2. Доменная логика в хендлерах (вынести в сервисы)

| Хендлер | Что считает | Куда |
|---|---|---|
| `client/my_lessons.py:78-104` | **деньги**: `build_billing_rows` по каждому занятию, суммы оплат по `(месяц, педагог)`, `lesson_paid_marks`, `direct_pay` → ✅/⬜ и «Не оплачено: N» | `PaymentService.student_lesson_marks(student, period)` → DTO `[LessonMark]`; общий с `teacher_lesson_marks` (ledger уже есть) |
| `api/miniapp.py:66-80` | остаток по педагогу = `total − paid_sums` — дубль `ledger_for` | использовать `ledger_for` / `unpaid_for` |
| `admin/payment_history.py:108-116, 160-170, 209-225` | суммы PAID / PENDING по ученику и периоду, иконки статуса | `PaymentService.payment_history(student_id)` → DTO |
| `admin/payouts.py::_detail_lines` (316-364) | группировка строк зарплаты по филиал/группа/индивид., суммы | `SalaryService.detail_breakdown()` → DTO; текст остаётся в хендлере |
| `admin/debtors.py::_collect_debtors` | `closed_total`, сортировка должников, «текущий месяц» | `PaymentService.debtors(current_period)` → `[DebtorRow]` |
| `teacher/my_lessons/guests.py:110-131` | цена гостя (`price_full` для PER_VISIT иначе 0) + правка `attendees` | `LessonService.add_guest(lesson, student, group)` (правило цены уже в `utils/attendees.build_group_attendees_csv`) |
| `admin/bills/helpers.py:27-29` | список инвойсов из ledger + `paid_total` | `PaymentService.invoices_for(student, period)` |
| `teacher/partners/lessons_history.py:110,222` | `total_min` | мелочь, оставить (форматирование) |
| `teacher/submit_period.py::_can_submit` | правило «с 25-го числа» | `LessonService`/`SubmissionService` (правило домена в хендлере) |
| `client/group_link.py`, `admin/students/client.py`, `max/handlers/start.py` | «создать клиента если нет + привязать к детям без client_id» — повторяется **4 раза** | `ClientService.ensure_client(tg_id, name, phone/email)` |
| `admin/students/requests.py`, `teacher/my_groups/members.py` | уведомления педагога/родителя с копипастом try/except | `utils/notify.py` уже есть — расширить |

Не считается нарушением: `sum(duration_min)`, подписи, иконки — форматирование.

---

## 3. Границы слоёв

- ✅ Репозитории нигде не создаются в хендлерах, gspread — только в `repositories/` и `scripts/`.
- ⚠️ `bot/services/parent_views.py::bill_detail` собирает **HTML-строки** (`<b>…</b>`) — экранная
  разметка в сервисе. Работает для TG и MAX одинаково, менять текст нельзя; переезд в
  `bot/screens/` — чистый перенос (низкий приоритет).
- ⚠️ `bot/api/miniapp.py` и `bot/max/handlers/payment.py` дублируют расчёт остатка вместо
  `ledger_for` (см. §2) — риск расхождения между фронтами.
- ⚠️ Хендлеры читают `settings.*` напрямую для доменных решений (`direct_pay_teacher_id_set`,
  `revenue_share_group_map`) в `client/my_lessons.py`, `my_lessons/detail.py`, `keyboards/teacher.py::kb_lesson_list`
  — должны приходить из сервисного DTO (флаг `is_direct_pay`, `is_revenue_share`).
- ⚠️ `keyboards/teacher.py::kb_lesson_list` импортирует `settings` и `attendee_ids` — клавиатура
  принимает доменные решения (какую иконку ставить занятию).
- ⚠️ `BaseRepository._cache` — class-level dict, общий для всех инстансов (нужен для I1/§6).

---

## 4. Покрытие тестами — что фиксировать до рефакторинга

### 4.1 Сервисы и утилиты

| Модуль | Тестов | Дыры, которые нужно закрыть характеризующими тестами |
|---|---|---|
| `billing_service` | 16 | ✅ |
| `payment_service` | ledger 14 + debt 10 + subscription 27 | `confirm_period`, `confirm_payment`, `confirm_teachers` (только через webhook), `create_yookassa_payment` (payload `receipt`), `teacher_lesson_marks` |
| `payment_ledger` | 14 | ✅ |
| `profit_service` | 7 + 3 (формат) | ✅ (после аренды зала) |
| `salary_service` | 3 | `compute_salary_lines` для обычных занятий (kind `lesson`), `SalaryService.lines_for` с фильтром по дню, `total_for` |
| `lesson_service` | 7 | `create()` — дата в будущем, замок периода без bypass, `delete()` с замком, `preview_period` |
| `student_service` | 27 | ✅ |
| `student_request_service` | 7 | ✅ |
| `diary_service` | 9 (чистые) | методы `DiaryService` не покрыты (низкий приоритет — не в периметре) |
| `visibility` | 5 | ✅ |
| `parent_views` / `screens/parent_bills` | 12 | `bills_periods` (иконки/подписи месяцев), `receipt_caption`/`cash_notice` |
| `membership`, `payment_methods`, `payment_watcher`, `parent_notifier`, `yookassa_webhook`, `telegram_auth`, `miniapp` | 5/3/3/5/8/5/4 | ✅ |
| `diagnostics_service` | **0** | `run_consistency_check` на фейках |
| `utils/bill_format.py::build_bill_text` | **0** | текст счёта родителю — **обязательно** (используется в рассылке) |
| `utils/lesson_stats.py`, `utils/groups.py`, `utils/notify.py`, `utils/diary_format.py` | **0** | простые golden-тесты |
| `utils/attendees`, `dates`, `ids`, `locks`, `group_links`, `diary_topics` | 15/5/6/5/10/3 | ✅ |
| `keyboards/*` | 0 (кроме wiring) | golden-снимки `kb_lesson_list`, `kb_student_paged`, `_kb_debtors`, `kb_student_card`, `kb_lesson_detail` |

### 4.2 Хендлеры

Покрытие = только `test_callback_wiring` (наличие хендлера) — **рендер экранов не зафиксирован
нигде**, а именно его затрагивают цели 3.2–3.4. Нужна инфраструктура golden-тестов:

- `tests/fakes.py`: `FakeMessage` / `FakeCallbackQuery` (записывают `edit_text/answer/answer_photo`
  с текстом и `reply_markup`), `FakeState`, фейковые репозитории с данными в памяти;
- golden-файлы `tests/golden/*.txt` с текстом экрана **до** правки; тест = «рендер на
  фиксированных данных == golden»;
- для callback-строк — корпус всех f-строк `callback_data=` (≈ 340 хендлеров, 254 разбора):
  roundtrip `unpack(pack(x)) == x` и `pack(unpack(s)) == s`.

Экраны, которые фиксируем перед соответствующими шагами: карточка ученика (админ/педагог),
карточка группы (админ/педагог), карточка педагога, `lesson_detail`, список занятий
(педагог/админ, все фильтры), список учеников с поиском/пагинацией, должники (пагинация +
напоминание), занятия родителя (✅/⬜, «Не оплачено», прямая оплата), история оплат,
расшифровка выплаты, экраны «Нет доступа» для каждой роли.

---

## 5. Приоритизированные шаги

Формат коммитов: `refactor(<слой>/<модуль>): <что сделано>`; один коммит — одно изменение;
после каждого — `.venv/bin/python -m pytest -q`. Риск: **Н** (низкий) / **С** (средний) / **В** (высокий).

### Блок 0 — инфраструктура (без изменений поведения)

| # | Шаг | Риск | Тесты до |
|---|---|---|---|
| 0.1 | `pyproject.toml`: секция `[project]` с `requires-python = ">=3.12"` (I5; факт: прод 3.12.3, venv 3.12.13, старая запись «venv 3.9» устарела) | Н | — |
| 0.2 | `ruff` + `mypy` в `requirements-dev.txt`, конфиг в `pyproject` (`line-length=120`, правила `E,F,W,B`; `I` — отдельным коммитом, т.к. переупорядочит импорты во всех файлах); mypy нестрого (`ignore_missing_imports`, без `strict`, `exclude = scripts/`) | Н | — |
| 0.3 | Прогон ruff/mypy, устранение находок только механических (неиспользуемые импорты, `== None`, `f"…"` без плейсхолдеров). Любая находка, меняющая логику, — в `FOUND_BUGS.md` | Н | ✅ существующие |
| 0.4 | `tests/fakes.py` + golden-инфраструктура (§4.2) | Н | — |

### Блок 1 — характеризующие тесты (этап B)

| # | Шаг | Риск |
|---|---|---|
| 1.1 | `build_bill_text`, `diagnostics_service`, `lesson_stats`, `groups.hide_service_groups` | — |
| 1.2 | `lesson_service.create/delete/preview_period`, `salary_service.lines_for/total_for`, `payment_service.confirm_*`, `teacher_lesson_marks`, `create_yookassa_payment` (payload) | — |
| 1.3 | Golden-снимки клавиатур с пагинацией и карточек (§4.2) | — |
| 1.4 | Корпус callback-строк + roundtrip-тест | — |
| 1.5 | Зафиксировать B2 тестом (заголовок мастера показывает «shared») — **не чинить** | — |

### Блок 2 — низкий риск, большой эффект

| # | Шаг | Риск | Что даёт |
|---|---|---|---|
| 2.1 | `bot/utils/paging.py` (`paginate`, `nav_row`); перевести `students/listing`, `debtors`, `kb_lesson_list` (`edit_lesson` через него) | Н | −3 копии; `_STUDENT_PAGE_SIZE` уходит из keyboards |
| 2.2 | `bot/utils/callbacks.py`: датаклассы для ~15 самых частых префиксов (`student_card`, `group_card`, `lesson_detail`, `lessons_page`, `client_pay`, `client_bill`, `pay_method`, `receipt_confirm`/`rcpp`, `paysel`/`pslt`, `salary_*`, `profit_*`, `payhist_*`, `aedl_*`) с `pack()` байт-в-байт; миграция по файлам — один файл = один коммит, начиная с топ-6 из §1.2 | С | −254 `split` постепенно; строки не меняются (roundtrip-тест) |
| 2.3 | N+1 (I4): предзагрузка `{id → entity}` там, где `get_by_id` в цикле: `client/my_lessons` (педагоги), `record_lesson/finalize` (ученики ×3), `record_lesson/flows`, `admin/students/groups` (группа+филиал), `student_service.get_student_card` (группа+филиал), `visibility.teachers_for_student`, `payment_service` (`_subscription_bills_for_student` — состав по группам), `api/miniapp` (счета: `ledger_for` вместо ручного остатка). Примечание: `get_by_id` у всех репозиториев — линейный скан `get_all()` с пересборкой dataclass'ов на каждый вызов (CPU-N+1 поверх TTL-кеша), поэтому эффект есть даже при тёплом кеше | Н | тесты на фейках с подсчётом вызовов + равенство результата |
| 2.4 | Общий помощник «состав группы» (`StudentService.members_of(group_id, key=…)`) и «имя филиала» с сохранением per-site fallback'ов (§1.4) | Н | −7 копий |
| 2.5 | `utils/notify.py::notify_safely` для 18 одинаковых try/except вокруг уведомлений | Н | −18 блоков |

### Блок 3 — средний риск (только после golden-тестов блока 1)

| # | Шаг | Риск | Примечание |
|---|---|---|---|
| 3.1 | Гарды как фильтры aiogram: `AdminOnly`, `TeacherOnly`, `TeacherOrAdmin` (`bot/handlers/filters.py`). Фильтр при отказе **сам** отвечает `«Нет доступа»` alert'ом и возвращает `False` — иначе исчезнет alert (сейчас его даёт тело хендлера). Проверено: catch-all `@router.callback_query()` в проекте нет, так что «пропущенный» апдейт никуда не провалится; это фиксируется тестом | С | вопрос Q1 |
| 3.2 | Вынос доменной логики из хендлеров (§2): по одному сервисному методу на коммит, начиная с `client/my_lessons` (деньги) и `payment_history` | С | тексты не меняются — golden |
| 3.3 | Карточки ученика/группы → DTO + билдеры в `bot/screens/` (`student_card.py`, `group_card.py`) с вариантом admin/teacher; хендлеры только `show_card` | С | golden на оба варианта |
| 3.4 | Типизация DTO вместо `dict` между слоями: `BillAggregate` (`compute_bills_for_student_period` — 10 потребителей), `UnpaidRow` (`unpaid_for`), `LessonMark` (`lesson_marks`), `DebtorRow`. FSM-словари (`selection_fsm_data`, `psel_*`) **остаются dict** — они сериализуются в Redis | С | по одному DTO на коммит; `__getitem__`-совместимость не делаем — переписываем потребителей в том же коммите |
| 3.5 | `parent_views.bill_detail` → `bot/screens/parent_bills.py` (чистый перенос HTML-сборки из сервиса) | Н | golden уже есть (`test_parent_screens`) |

### Блок 4 — высокий риск (отдельное согласование)

| # | Шаг | Риск | Примечание |
|---|---|---|---|
| 4.1 | I1 — гонка read-modify-write в `BaseRepository`: per-sheet `asyncio.Lock` вокруг «найти строку → записать/удалить» + перед записью повторный поиск строки **по ID-ячейке** (`ws.find(value, in_column=col)`, один API-вызов) вместо индекса из кеша; при несовпадении — `RowMovedError` и повтор. Меняет число API-вызовов на запись (+1 `find`) | В | тесты на конкурентные сценарии с фейковым `Worksheet` (две корутины: удалить строку выше и обновить строку ниже); вопрос Q2 |

### Вне периметра (по ТЗ)

Не делаем: I2 (Redis-локи), I3, I6 (`format_money`), I8; B2 — фиксируем тестом, не чиним;
`SHEET_BILLING`, `Procfile`, `WEBHOOK_URL` остаются.

---

## 6. Порядок выполнения и контрольные точки

1. Блок 0 → блок 1 (этап B) → отчёт.
2. Блок 2 (2.1 → 2.5) → отчёт.
3. Блок 3 (3.5 → 3.1 → 3.2 → 3.3 → 3.4) → отчёт.
4. Блок 4 — только после явного согласия.
5. Этап D: сравнить `bot/__main__.py` до/после — набор роутеров
   (`common, admin, teacher, athlete, client` + 13 admin-подроутеров, 8 teacher, 7 client),
   middleware (`DedupUpdateMiddleware` outer → `AuthMiddleware`), **31 DI-ключ** `dp[...]`
   (снимок ниже); `test_callback_wiring` + `test_max_front`; обновить `CLAUDE.md`, `README.md`,
   `AGENTS.md` (там до сих пор «три роли» и «14 репозиториев»).

Снимок DI-ключей (эталон для этапа D): `user_repo, teacher_repo, student_repo, lesson_repo,
payment_repo, submission_repo, branch_repo, group_repo, teacher_group_repo, student_group_repo,
student_request_repo, client_repo, subscription_override_repo, finance_entry_repo,
rate_history_repo, payout_repo, salary_override_repo, salary_service, lesson_service,
payment_service, profit_service, diagnostics_service, visibility, student_service,
student_request_service, cloudkassir_service, notifier, training_entry_repo, athlete_task_repo,
diary_service` (+ `notifier.max_bot` при MAX).

---

## 7. Найденные по пути дефекты и сомнительные места

Занесены в `FOUND_BUGS.md` / `IMPROVEMENTS.md` (без исправления):

- **B5** `admin/payment_history.py:108-116` — статус ученика в списке группы считает **все**
  периоды (⏳ если есть любой pending > 0), а «Должники» — только с `DEBTORS_SINCE_PERIOD`;
  один и тот же ученик может быть ⏳ в истории и отсутствовать в должниках.
- **B6** `bot/api/miniapp.py:66-80` — остаток по педагогу считается вручную
  (`total − paid_sums`), минуя `ledger_for`: строка-остаток в листе не синхронизируется,
  и при переплате Mini App покажет 0, а бот — «переплата».
- **B7** `teacher/my_lessons/guests.py:110-113` — гость в PER_VISIT-группе всегда получает
  `price_full`, тариф SHORT ученика (`student.group_tier`) не учитывается, в отличие от записи
  занятия (`build_group_attendees_csv`).
- **I9** `BaseRepository.get_by_id` во всех репозиториях — линейный скан с пересборкой всех
  dataclass'ов на каждый вызов (см. 2.3).
- **I10** `AGENTS.md` расходится с `CLAUDE.md` (три роли, 14 репозиториев, aiogram 3.13) —
  обновить на этапе D.
- **I5** — устарела формулировка: локальный venv уже 3.12.13; остаётся только добавить
  `requires-python`.

---

## 8. Вопросы, требующие решения

- **Q1 (шаг 3.1).** Фильтр доступа, который сам показывает alert «Нет доступа», — это фильтр с
  побочным эффектом. Альтернатива без побочных эффектов — декоратор `@admin_only` внутри
  хендлера (та же семантика, что сейчас, но не «фильтр aiogram»). Какой вариант предпочесть?
  Рекомендую фильтр + тест «нет catch-all хендлеров», как в плане.
- **Q2 (шаг 4.1).** I1 добавляет по одному API-вызову `find` на каждую запись/удаление
  (лимит Sheets — 60 запросов/мин на пользователя). Согласны на это, или ограничиться только
  per-sheet `asyncio.Lock` (защищает от гонки внутри процесса, но не от параллельных правок
  таблицы руками)?
- **Q3 (шаг 0.2).** `ruff` с правилом `I` (сортировка импортов) тронет почти каждый файл одним
  коммитом. Включать сразу или отложить?
- **Q4 (шаг 2.2).** Мигрировать на `bot/utils/callbacks.py` все 254 разбора или только топ-6
  файлов (≈ 60) и дальше по мере правок? Полная миграция — ~25 коммитов.
