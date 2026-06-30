# CLAUDE.md

Guide for Claude Code (claude.ai/code) working in this repository. Project: **fokus-bot** — Telegram bot (aiogram 3) for a dance-school CRM, using **Google Sheets as the database**. Three roles: **admin**, **teacher**, **client (parent)**.

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

No test suite or linter is configured. Manual verification only — run the app, exercise the flow in Telegram, check production logs.

Python 3.12+. Main deps: `aiogram 3.13`, `gspread 6`, `pydantic 2`, `pydantic-settings`, `python-dotenv`, `redis`, `yookassa`, `qrcode[pil]`. Full list in [requirements.txt](requirements.txt).

---

## Architecture

### Entry point & wiring

`bot/__main__.py` is the single DI container. Order matters:

1. Build **`SheetsClient`** from settings (one shared gspread client, caches Worksheet handles).
2. Construct **13 repositories** (one per Google Sheets tab), all inheriting `BaseRepository`.
3. Construct **6 services**: `LessonService`, `PaymentService`, `DiagnosticsService`, `TeacherVisibilityService` (via [bot/services/visibility.py](bot/services/visibility.py)), `CloudKassirService`.
4. Pick **FSM storage**: `RedisStorage` if `REDIS_URL` is set, else `MemoryStorage`.
5. Register two middlewares (in this order):
   - **`DedupUpdateMiddleware`** (outer) — drops re-delivered Telegram updates by `m:{chat_id}:{message_id}` / `c:{callback_query.id}` key with 60 s TTL, GC after 256 entries.
   - **`AuthMiddleware`** (with `user_repo`) — loads `User` by `tg_id`, injects into `data["user"]` (or `None` for unknown). Also logs per-update latency.
6. Inject every repo / service into the dispatcher via `dp["key"]`. Handlers receive them as typed parameters — aiogram resolves the names automatically.
7. Include four top-level routers: `common_router`, `admin_router`, `teacher_router`, `client_router`.
8. Webhooks:
   - Telegram: `/webhook/{bot_token}` if `WEBHOOK_URL` is set, otherwise polling.
   - YooKassa: `/yookassa-webhook` always registered on `PAYMENT_WEBHOOK_PORT` (default 8081).

### Handlers

```
bot/handlers/
  common.py              # /start, /menu, mode:admin/teacher, go:home, noop
  admin/
    teachers.py          # teacher CRUD, rates, groups, submission status icons
    students.py          # student CRUD, partnerships, client linking, search
    client_requests.py   # approve/reject parent→student link requests (admin_child_ok/no)
    salaries.py          # teacher earnings by period (salary_teacher/period)
    bills.py             # student invoices: view → send to parent; multi-recipient
    profit.py            # «Выручка»: school revenue vs salary summary
    branches.py          # branches/groups CRUD, teacher↔group, group billing, bulk send
    edit_lesson.py       # admin-only lesson view/delete (bypasses period lock)
    diagnostics.py       # consistency check (orphan lessons etc.)
    record_lesson.py     # proxy: admin records lessons on behalf of a teacher
  teacher/
    record_lesson.py     # FSM RecordLessonStates: type → students/group → date → duration
    my_lessons.py        # view/delete own lessons; lesson_detail shared with admin
    submit_period.py     # lock period for billing (allowed from 25th of month)
    my_groups.py         # group roster; add student via search/create
    partners.py          # pairs/soloists; student card; lesson history by student/pair
    my_stats.py          # personal earnings summary
    billing.py           # 💰 «Счета учеников» for teachers in BILLING_TEACHERS (Контарева)
  client/
    start.py             # client registration FSM (search → confirm) + admin approval
    my_lessons.py        # lesson history; ✅ on paid; per-child or "all"
    my_bills.py          # invoices by month + payment methods + receipt upload
    payments.py          # YooKassa webhook handler + Telegram Payments pre_checkout
```

Each handler file exports one `Router`. Aggregated in `bot/handlers/{admin,teacher,client}/__init__.py`, then in `bot/handlers/__init__.py`, registered in `bot/__main__.py`.

### Repositories

All inherit `BaseRepository` ([bot/repositories/base.py](bot/repositories/base.py)) which wraps gspread inside `asyncio.to_thread()` and provides:

- **TTL cache** (300 s) on every read; any write to that sheet invalidates the cache for that sheet.
- **Retry**: 3 attempts with exponential backoff on HTTP 429 / 503 / network errors.
- 1-based row indexing (row 1 = header, row 2+ = data).

| Repo class | Google Sheets tab | Purpose |
|---|---|---|
| `UserRepository` | `users` | tg_id → User; is_admin, optional teacher_id |
| `TeacherRepository` | `teachers` | Teacher cards + 3 rates (group / for_teacher / for_student) |
| `StudentRepository` | `students` | Students; partners (symmetric); parent_tg_ids (pipe-separated); client_id; tier |
| `LessonRepository` | `lessons` | All lessons; query by teacher/period/student; individual_lesson_exists guard |
| `PaymentRepository` | `student_payments` | `StudentPeriodPayment` rows; confirm single or batch-per-period |
| `TeacherPeriodSubmissionRepository` | `teacher_submissions` | "Period submitted" rows — used as a lock |
| `BranchRepository` | `branches` | School branches |
| `GroupRepository` | `groups` | Groups; billing_mode + per-tier prices/durations |
| `TeacherGroupRepository` | `teacher_groups` | Many-to-many teacher ↔ group (join table) |
| `StudentGroupRepository` | `student_groups` | Many-to-many student ↔ group (join table) |
| `ClientRepository` | `clients` | Parent entities; phone (normalized) + optional tg_id |
| `StudentRequestRepository` | `student_requests` | Teacher-submitted requests to add a new student (admin approves) |

**Google Sheets locale gotcha**: Russian-locale spreadsheets interpret `,` as a decimal separator. Any multi-value field written as comma-separated integers will be silently corrupted (`"123,456"` → `123.456` → `123`). Use `|` as separator. See `student.parent_tg_ids` (parser still accepts `,` for backwards compatibility).

### Services

| Service | Responsibility |
|---|---|
| `LessonService` | `create()` / `create_pair_batch()` / `create_soloist_batch()` / `delete()`. All accept `bypass_period_lock: bool` (admins pass `True`). Solo duplicates blocked via `individual_lesson_exists`; group duplicates intentionally **not** blocked (one group can have multiple shifts per day). |
| `BillingService` ([billing_service.py](bot/services/billing_service.py)) | Pure functions only: `calc_earned()` (teacher salary) and `build_billing_rows()` (virtual per-student Billing rows, computed on demand from a Lesson + Teacher). |
| `PaymentService` | `compute_bills_for_student_period()`, `get_or_create_invoices_for_student_period()`, `confirm_period()` (batch PENDING → PAID), `confirm_payment()` (single), `create_yookassa_payment()` (returns confirmation URL). Does **not** check submission status — admins/billing teachers can issue bills anytime. |
| `TeacherVisibilityService` | Who can see whom: `students_for_teacher()`, `students_in_group_for_teacher()`, `teachers_for_student()`, `is_visible()`. Pure intersection of `teacher_groups` and `student_groups`. |
| `DiagnosticsService` | `run_consistency_check()` → `DiagnosticsReport` (orphan lessons referencing missing teachers/students). |
| `CloudKassirService` | `send_income_receipt(phone, student_name, period_month, amount)` — fires a fiscal receipt; phone is normalized to `+7…`. No-op if `CLOUDKASSIR_PUBLIC_ID` is empty. |

### Models & enums

Dataclasses in [bot/models/entities.py](bot/models/entities.py): `User`, `Teacher`, `Student`, `Lesson`, `Billing` (virtual), `StudentPeriodPayment`, `TeacherPeriodSubmission`, `Branch`, `Group`, `TeacherGroup`, `StudentGroup`, `Client`, `StudentRequest`.

Enums in [bot/models/enums.py](bot/models/enums.py):
- `LessonType`: `GROUP` | `INDIVIDUAL`
- `PaymentStatus`: `PENDING` | `PAID`
- `RequestStatus`: `PENDING` | `APPROVED` | `REJECTED`
- `GroupBillingMode`: `NONE` | `PER_VISIT` | `SUBSCRIPTION` (last one not implemented)
- `StudentGroupTier`: `FULL` | `SHORT` (used only by kindergarten groups ЮБ/БП)

### Utils

- [bot/utils/attendees.py](bot/utils/attendees.py) — parse/serialize `lesson.attendees`. Two CSV formats:
  - Old: `STU-001,STU-002` (no per-student amount → `amount=0` → not billed, shown as «абонемент»)
  - New: `STU-001:60:700,STU-002:60:700` (with `duration_min:amount` snapshot → billed)
  - `parse_attendees()`, `serialize_attendees()`, `attendee_ids()`, `AttendeeEntry` dataclass.
- [bot/utils/bill_format.py](bot/utils/bill_format.py) — `build_bill_text()`. Shared parent-facing bill renderer used by both `admin/bills.py` and `teacher/billing.py`.
- [bot/utils/dates.py](bot/utils/dates.py) — date helpers (`now_str`, `format_date_display`, `period_month_from_date`, `display_period`, `format_date_short_with_wd`). Formats: storage `YYYY-MM-DD` / `YYYY-MM`, display `ДД.ММ.ГГГГ` / `ММ.ГГГГ`.
- [bot/utils/ids.py](bot/utils/ids.py) — sequential ID generators: `TCH-XXXX`, `STU-XXXX`, `LES-XXXXXX`, `GRP-XXXX`, `BRN-XXX`, `PAY-XXXXXX`, `SUB-XXXXXX`, `USR-XXXX`, `INV-XXXXXX`. Each scans existing rows for the current max.
- [bot/utils/lesson_stats.py](bot/utils/lesson_stats.py) — `format_lesson_breakdown(lessons) → (group_count, ind_count, group_line, ind_line)` for stats screens.

### Keyboards

Layout in `bot/keyboards/` by role: `admin.py`, `teacher.py`, `client.py`, `common.py`, `calendar.py`. Functions named `kb_*` return `InlineKeyboardMarkup`. Two important module-level constants live in [bot/keyboards/teacher.py](bot/keyboards/teacher.py):

- `_PROXY_BUTTONS: dict[teacher_id → list[(label, callback)]]` — extra buttons in the teacher menu. Used for Клецова (TCH-0002 → record for TCH-0005/0008).
- `BILLING_TEACHERS: set[str]` — teachers with access to the billing UI. Currently `{"TCH-0009"}` (Контарева).

`kb_lesson_detail()` takes an `is_admin: bool` flag — when `True`, admins see the delete button even for locked lessons (with `(🔒 период сдан)` suffix).

### FSM states

In `bot/states/`. Each multi-step flow has its own `StatesGroup`. Some highlights:

- `RecordLessonStates` (~17 states) — the lesson-recording wizard. Lives in [bot/handlers/teacher/record_lesson.py](bot/handlers/teacher/record_lesson.py).
- `SubmitPeriodStates` — month picker → confirmation.
- `AddTeacherStates`, `EditTeacherRatesStates`, `AddStudentStates`, `PartnerAssignStates`, `ConfirmPaymentStates`, `StudentListStates` — admin flows.
- `AddBranchStates`, `EditBranchNameStates`, `AddGroupStates`, `EditGroupNameStates`, `GroupBillingStates`, `GroupAddStudentStates` — branch/group flows.
- `TeacherRenameStudentStates`, `TeacherGroupAddStudentStates` — teacher flows.
- `ClientCreateStates`, `ClientRegStates` — client side.
- `ReceiptStates` — uploading a payment receipt (client → admin).

**Admin proxy pattern**: `proxy_teacher_id` in FSM state lets an admin (or Клецова) record lessons on behalf of another teacher. Helper `_tid(user, data)` returns `data.get("proxy_teacher_id") or user.teacher_id`. Proxy by Клецова requires admin approval (`proxy_approve` / `proxy_deny` / `proxy_record_go` in `teacher/record_lesson.py`); admins bypass approval entirely.

**Lesson-history back-nav**: FSM key `t_stu_les_back` stores the return callback when a teacher opens a lesson detail from a student/pair card. `cb_lesson_detail` in `my_lessons.py` honors it before falling back to the default.

### Race-condition guards

Module-level `set` lockers are sprinkled where double-clicks would corrupt data:
- `_confirming_lesson_ids` in `teacher/record_lesson.py` (per tg_id)
- `_submitting` in `teacher/submit_period.py`
- `_sending_in_progress`, `_confirming_in_progress` in `admin/bills.py`
- `_sending`, `_group_sending` in `teacher/billing.py`
- `_seen` in `DedupUpdateMiddleware`

### ID format

Human-readable sequential strings; never autoincrement integers. See `bot/utils/ids.py`.

---

## Billing formulas

```
earned (teacher) = rate × (duration_min / 45)
  rate = rate_group          for group lessons
  rate = rate_for_teacher    for individual lessons

amount (student invoice) = rate_for_student × (duration_min / 45)
  split equally across all students; first student absorbs the integer remainder

amount (PER_VISIT group) = group.price_full or group.price_short
  stored as snapshot in lesson.attendees at creation time: STU-001:60:700
```

`Billing` rows are virtual — they are computed on demand by `build_billing_rows(lesson, teacher)` from a Lesson + Teacher. Nothing is stored in a `billings` sheet (the model exists for shape).

---

## Payment flow (client side)

Invoices stored as `StudentPeriodPayment` — one per `(student, teacher, period)`. Statuses: `PENDING` / `PAID`.

**Lessons have no payment status field.** The ✅ in the «Занятия» screen is computed on the fly: for each lesson, check whether `(date[:7], teacher_id)` exists in `StudentPeriodPayment` with `status=PAID`.

**Bill detail** ([my_bills.py: cb_bill_detail](bot/handlers/client/my_bills.py)):
- Paid teachers shown with ✅ and «оплачено» label; their amount excluded from "К оплате".
- "К оплате" = sum of only unpaid teachers' invoices.
- "Оплатить" button hidden when nothing is left to pay.

**Payment methods** (each configured via ENV — invisible if the corresponding setting is empty):
1. 💵 Наличные — admin gets notification + confirm button (`PAYMENT_CASH_ENABLED`).
2. 🏦 По реквизитам — shows QR + bank details; client uploads receipt (`PAYMENT_BANK_DETAILS`).
3. 📱 СБП — shows SBP details; client uploads receipt (`PAYMENT_SBP_DETAILS`).
4. 💳 Картой онлайн — YooKassa link (`YOOKASSA_SHOP_ID` + `YOOKASSA_SECRET_KEY`).

Receipt upload uses FSM `ReceiptStates.waiting_for_receipt` ([bot/states/client_states.py](bot/states/client_states.py)). On receipt: admins receive the photo/document with a «✅ Подтвердить оплату» button. Admin confirms → `confirm_period()` → all PENDING invoices for that `(student, period)` → PAID. If `CLOUDKASSIR_PUBLIC_ID` is set and the linked client has a phone, `CloudKassirService.send_income_receipt()` then fires a fiscal receipt for the paid amount.

**Important**: if a lesson is added to a period after its invoice was marked PAID, the new amount will **not** be auto-billed — `confirm_period` skips PAID records. Either reopen the period (admin via «🔓 Открыть период») or create a second invoice manually.

---

## Key domain rules

- **Period submission**: once submitted, the teacher can no longer edit/delete lessons in that period.
  - **Admins (and `bypass_period_lock=True` callers) can both create AND delete** lessons in a submitted period — both `LessonService.create()` and `LessonService.delete()` accept the flag.
  - `kb_lesson_detail` renders «🗑 Удалить занятие» for admins even when locked (via `is_admin=True`).
  - Teachers can submit a period only from the **25th of the month**.
  - Admins reopen a whole period via teacher card → «🔓 Открыть период» (deletes the submission row entirely).
- **Admin bills bypass submission**: admins (and teachers in `BILLING_TEACHERS`) can issue/send a bill to a parent at any moment — no «period not submitted» blocker. Bill amounts are recomputed from current lessons on each open.
- **Lesson names are denormalized**: `lesson.teacher_name`, `student_N_name` are creation-time snapshots. Renames don't rewrite history.
- **Teacher visibility**: derived from `teacher_groups` ∩ `student_groups`. There is **no** `teacher_students` table.
- **Multi-group students**: a student can belong to multiple groups; billing aggregates across all per period.
- **SHORT/FULL tiers** (`StudentGroupTier`): only for kindergarten groups (ЮБ/БП); all others have one price.
- **Group billing modes**: `NONE` (free, attendance not billed), `PER_VISIT` (each attended lesson billed at group price), `SUBSCRIPTION` (not implemented).
- **NONE groups auto-save**: recording a group lesson for a NONE-mode group skips attendance and saves immediately with `attendees=None`.
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
| Bills | `admin:bills` | Invoices per student per period; multi-recipient send to parent (no submission check) |
| Выручка | `admin:profit` | School revenue vs salary summary; by day or by period |
| Branches/Groups | `admin:branches` | CRUD branches, groups, billing modes, prices; bulk bill send per group |
| Edit lessons | `admin:edit_lesson` | Pick teacher → date/month/all → view+delete (bypasses period lock) |
| Diagnostics | `admin:diagnostics` | Cache and data health checks |
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
| Proxy record (Клецова) | `proxy_record:TCH-0005/0008` | Requires admin approval; then same FSM as own record |
| **💰 Bills for own groups** | `teacher:bills` | Only for teachers in `BILLING_TEACHERS` (Контарева TCH-0009). Period → group → student card / bulk send. Scope strictly limited to teacher's own groups (`tb:p / tb:g / tb:s / tb:snd / tb:all` callbacks) |

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

---

## Special roles & configuration

**Клецова Ангелина (TCH-0002)** — assistant. Her menu has extra proxy buttons for Никишин (TCH-0005) and Криворчук (TCH-0008). Configured in `_PROXY_BUTTONS` in [bot/keyboards/teacher.py](bot/keyboards/teacher.py). Proxy requests require admin approval (see "FSM states" → admin proxy pattern).

**Контарева Елизавета (TCH-0009)** — teacher with billing access. Her menu has an extra «💰 Счета учеников» button (handlers in [bot/handlers/teacher/billing.py](bot/handlers/teacher/billing.py)). Scope is strictly her own groups: every callback re-validates `_can_bill(user)`, `_is_my_group(...)`, and `_is_student_in_group(...)`. Bill content is the same as the admin one (combines lessons of all teachers for that student) — she sees the «full» bill, not just her own lessons. Whitelist defined as `BILLING_TEACHERS = {"TCH-0009"}`. Add a teacher_id to that set to grant the same access.

**PER_VISIT groups** (each attended lesson billed):
- GRP-0007, GRP-0008, GRP-0010, GRP-0015 — various groups
- GRP-0017 «БП Спортивная — Никишин» (TCH-0005): `price_full=700`
- GRP-0018 «БП Спортивная — Криворчук» (TCH-0008): `price_full=700`

---

## Scripts & operations

`scripts/` contains both deployment helpers and one-off maintenance scripts:

| File | Purpose |
|---|---|
| `deploy.sh` | Production deploy — rsync working tree to `root@178.104.240.252:/opt/fokus-bot/` (excludes `.git`, `.venv`, `.env`, credentials, `bot.log`, `__pycache__`), then `systemctl restart fokus-bot && journalctl -n 20`. Standard deploy command for this repo. |
| `audit_teacher_students.py` | Read-only consistency check: every student's visibility to each teacher matches `teacher_groups ∩ student_groups`. |
| `migrate_student_groups.py` | One-shot migration from legacy `students.group_id` column to the `student_groups` join table. Idempotent. |
| `bulk_seed_2026_04.py` | One-shot seeding of students + group assignments for a specific intake (April 2026). Has `--dry-run` and `--apply` flags. |
| `send_bills_grp0004.py` | Template script for ad-hoc bill mailings to one group. Parametrized at the top (`GROUP_ID`, `PERIOD`, `RECIPIENT`). |

Production: **Hetzner VPS (Nuremberg)**, systemd unit `fokus-bot.service`, deployed by `./scripts/deploy.sh`. Logs via `journalctl -u fokus-bot`. The Railway-related `WEBHOOK_URL` / `Procfile` are present but unused — current production runs in polling mode under systemd.

---

## Environment

Required:
- `BOT_TOKEN` — Telegram bot token
- `GOOGLE_CREDENTIALS_JSON` — service account JSON (inline, single-line)
- `SPREADSHEET_ID` — main Google Spreadsheet ID

Optional — Google Sheets tab names (have sensible defaults — only set to override): `SHEET_USERS`, `SHEET_TEACHERS`, `SHEET_STUDENTS`, `SHEET_LESSONS`, `SHEET_BILLING`, `SHEET_PAYMENTS`, `SHEET_TEACHER_PERIOD_SUBMISSIONS`, `SHEET_BRANCHES`, `SHEET_GROUPS`, `SHEET_TEACHER_GROUPS`, `SHEET_STUDENT_GROUPS`, `SHEET_STUDENT_REQUESTS`, `SHEET_CLIENTS`.

Optional — payments:
- `PAYMENT_CASH_ENABLED` — show cash payment option (default `True`)
- `PAYMENT_BANK_DETAILS` — bank details text (`\n` becomes a newline; handler replaces `\\n` → `\n`)
- `PAYMENT_QR_DATA` — ЦБ РФ format string for QR generation (`ST00012|Name=...|PersonalAcc=...`)
- `PAYMENT_QR_IMAGE_URL` — fallback public HTTPS URL for QR image
- `PAYMENT_SBP_DETAILS` — SBP details text (phone / link)
- `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — YooKassa credentials
- `YOOKASSA_RETURN_URL` — return URL after YooKassa payment (default `https://t.me/fokus_bot`)
- `PAYMENT_WEBHOOK_PORT` — port for the YooKassa webhook aiohttp server (default `8081`)
- `CLOUDKASSIR_PUBLIC_ID`, `CLOUDKASSIR_API_SECRET` — fiscal receipt service; if empty, fiscal receipts are skipped silently

Optional — infrastructure:
- `WEBHOOK_URL` — if set, bot runs in webhook mode at `/webhook/{bot_token}` (currently unused in production)
- `PORT` — HTTP port (default `8080`; Railway sets automatically)
- `REDIS_URL` — FSM state persistence across restarts (`MemoryStorage` if absent)

A `.env.example` and `Procfile` (`worker: python -m bot`) are at the repo root for Railway/dev convenience; current production does not use Railway.
