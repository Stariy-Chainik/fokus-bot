# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run locally (polling mode)
python -m bot

# Deploy to production VPS
./scripts/deploy.sh

# View production logs
ssh root@178.104.240.252 journalctl -u fokus-bot -f

# Quick data lookup (example)
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

No test suite or linter is configured.

## Architecture

**Telegram bot** (aiogram 3) for a dance school CRM. **Google Sheets is the database** — every read/write goes through `gspread`. Three roles: admin, teacher, client (parent).

### Entry point & wiring

`bot/__main__.py` is the single DI container: builds all repositories from one `SheetsClient`, wires them into services, injects everything into the aiogram dispatcher via `dp["key"]`. Handlers receive dependencies as typed parameters (aiogram resolves them automatically).

Two middlewares on every update:
1. **`DedupUpdateMiddleware`** — drops re-delivered Telegram updates (60 s TTL)
2. **`AuthMiddleware`** — loads `User` from `user_repo`, injects `data["user"]`; `None` if unknown

### Handlers

```
bot/handlers/
  common.py              # /start, role switching, go:home
  admin/
    teachers.py          # teacher CRUD, rates, groups, open submitted period
    students.py          # student CRUD, partnerships, client linking
    client_requests.py   # approve/reject parent→student link requests
    salaries.py          # teacher earnings by period
    bills.py             # student invoices, send to parent
    profit.py            # school revenue (Выручка) vs salary summary
    branches.py          # branches, groups, teacher↔group assignment
    edit_lesson.py       # admin-only lesson view/delete (bypasses period lock)
    diagnostics.py       # data integrity checks
    record_lesson.py     # admin proxy: record lessons on behalf of any teacher
  teacher/
    record_lesson.py     # FSM: record individual / pair / group lessons
    my_lessons.py        # view and delete own lessons; lesson_detail shared with admin
    submit_period.py     # lock period for billing (allowed from 25th of month)
    my_groups.py         # group roster with student cards
    partners.py          # pairs/soloists, student card, lesson history by student/pair
  client/
    start.py             # client registration + add-child flow
    my_lessons.py        # lesson history for parent (with ✅ paid status)
    my_bills.py          # invoices by month + payment flow
    payments.py          # YooKassa webhook handler
```

Each file exports a single `Router`. Aggregated in `__init__.py`, registered in `__main__.py`.

### Repositories

Inherit `BaseRepository` (`bot/repositories/base.py`) which wraps gspread in `asyncio.to_thread()`:
- **TTL cache** (300 s) on all reads; invalidated on any write to that sheet
- **Retry** — 3 attempts with exponential backoff on HTTP 429/503

**Google Sheets locale bug**: Russian-locale spreadsheets interpret `,` as a decimal separator. Any multi-value field written as comma-separated integers will be silently corrupted (e.g. `"123,456"` → `123.456` → `123`). Use `|` as separator for such fields. See `student.parent_tg_ids`.

### Services

| Service | Responsibility |
|---|---|
| `LessonService` | Create/delete lessons; period-lock check; duplicate group lesson guard |
| `BillingService` | Pure functions: `calc_earned()` and `build_billing_rows()` |
| `PaymentService` | Invoice computation; `get_or_create_invoices_for_student_period()`; `confirm_period()`; `create_yookassa_payment()` |
| `TeacherVisibilityService` | Which students/groups a teacher can see (via `teacher_groups` + `student_groups`) |
| `DiagnosticsService` | Cache/data health checks |

### Billing formulas

```
earned (teacher) = rate × (duration_min / 45)
  rate = rate_group          for group lessons
  rate = rate_for_teacher    for individual lessons

amount (student invoice) = rate_for_student × (duration_min / 45)
  split equally across all students; first student absorbs integer remainder

amount (PER_VISIT group) = group.price_full or group.price_short
  stored as snapshot in lesson.attendees at creation time: STU-001:60:700
```

Group attendance is stored as a CSV in `lesson.attendees`:
- Old: `STU-001,STU-002` (no amounts → amount=0 → not billed, shown as «абонемент»)
- New: `STU-001:60:700,STU-002:60:700` (with amount snapshot → billed)

Parsed by `bot/utils/attendees.py`.

### Payment flow (client side)

Invoices: one `StudentPeriodPayment` per (student, teacher, period). Statuses: `PENDING` / `PAID`.

**Lessons have no payment status field.** ✅ in «Занятия» screen is computed on the fly: for each lesson, check if `(date[:7], teacher_id)` exists in `StudentPeriodPayment` with status `PAID`.

**Bill detail** (`my_bills.py: cb_bill_detail`):
- Paid teachers shown with ✅ and «оплачено» label; their amount excluded from "К оплате"
- "К оплате" = sum of only unpaid teachers' invoices
- "Оплатить" button hidden when nothing left to pay

**Payment methods** (configured via ENV):
1. 💵 Наличные — admin gets notification + confirm button (`PAYMENT_CASH_ENABLED`)
2. 🏦 По реквизитам — shows QR + bank details; client uploads receipt (`PAYMENT_BANK_DETAILS`)
3. 📱 СБП — shows SBP details; client uploads receipt (`PAYMENT_SBP_DETAILS`)
4. 💳 Картой онлайн — YooKassa link (`YOOKASSA_SHOP_ID` + `YOOKASSA_SECRET_KEY`)

Receipt upload uses FSM `ReceiptStates.waiting_for_receipt` (`bot/states/client_states.py`). On receipt: admins receive photo/document with «✅ Подтвердить оплату» button. Admin confirms → `confirm_period()` → all PENDING invoices for that (student, period) → PAID.

**Important**: if a lesson is added to a period after its invoice is already PAID, the new amount will NOT be automatically billed (confirm skips PAID records).

### FSM

States in `bot/states/`. Multi-step flows use aiogram FSM with explicit Back/Cancel at each step. State cleared on cancel or completion.

**Admin proxy pattern**: `proxy_teacher_id` in FSM state lets admin or Клецова record on behalf of another teacher. Helper `_tid(user, data)` returns `data.get("proxy_teacher_id") or user.teacher_id`.

**Proxy approval**: when Клецова (TCH-0002) initiates a proxy record, admins receive a confirmation request. After admin approves, Клецова gets a «▶ Начать запись» button. Admins bypass approval entirely. Handlers: `proxy_approve`, `proxy_deny`, `proxy_record_go` in `bot/handlers/teacher/record_lesson.py`.

**Lesson history back-nav**: FSM key `t_stu_les_back` stores return callback when teacher opens lesson detail from student/pair card. `cb_lesson_detail` in `my_lessons.py` checks this key before falling back to default.

### ID format

All primary keys are human-readable sequential strings:
`TCH-0001`, `STU-0042`, `LES-000123`, `GRP-0001`, `BRN-001`, etc.
Generated by `bot/utils/ids.py` which scans existing rows for the current max.

---

## Full feature inventory

### Admin

| Feature | Entry point | Notes |
|---|---|---|
| Teacher list | `teachers:list` | Shows 🟢/🔴 submission status for prev month |
| Teacher card | `teacher_card:{id}` | Rates, groups, submission history |
| Add teacher | `teachers:add` | FSM: tg_id → name → 3 rates |
| Edit rates | `card_edit_rates:{id}` | FSM: pick rate type → enter value |
| Edit groups | `t_edit_groups:{id}` | Checkbox list, confirm/cancel |
| Delete teacher | `del_teacher:{id}` | Confirmation screen |
| **Open submitted period** | `open_period_list:{id}` | Lists submitted periods; deletes chosen submission row → teacher can edit again |
| Student list | `students:list` | Search by name |
| Student card | `student_card:{id}` | Partner, group, client links |
| Add student | `students:add` | Name only |
| Rename student | `student_rename:{id}` | |
| Delete student | `student_delete:{id}` | Clears partner link first |
| Manage partner | `student_set_partner:{id}` / `student_clear_partner:{id}` | |
| Link client | `student_link_client:{id}` | Associates student with a Client entity |
| Approve child request | `admin_child_ok:{tg_id}:{student_id}` | Sent by parent via `client:add_child` |
| Salaries | `admin:salaries` | Earnings per teacher per period |
| Bills | `admin:bills` | Invoices per student per period; send to parent |
| Выручка | `admin:profit` | School revenue vs salary summary |
| Branches/Groups | `admin:branches` | CRUD branches, groups, billing modes, prices |
| Edit lessons | `admin:edit_lesson` | Pick teacher → date/month/all → view+delete |
| Diagnostics | `admin:diagnostics` | Cache and data health |
| Record for teacher | `admin:record_lesson` | Proxy: pick teacher → full lesson FSM |

### Teacher

| Feature | Entry point | Notes |
|---|---|---|
| Record lesson | `teacher:record_lesson` | FSM: type → students/group → date → duration → confirm |
| View lessons | `teacher:lesson_view` | Filter: today/yesterday/date/month; type toggle; paged |
| Delete lesson | `teacher:lesson_delete` | Same filters; locked lessons hidden |
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
| Admin confirms payment | `receipt_confirm:{stu}:{month}` | → `confirm_period()` → all PENDING → PAID |
| Cash notify | `cash_notify:{stu}:{month}` | Sends admin notification with confirm button |

---

## Key domain rules

- **Period submission**: once submitted, lessons are locked — no edits/deletes except by admin. Teachers can submit only from the **25th of the month**. Admins can reopen (delete submission) via teacher card → «🔓 Открыть период».
- **Lesson names are denormalized**: `lesson.teacher_name`, `student_N_name` are creation-time snapshots. Renames don't rewrite history.
- **Teacher visibility**: derived from `teacher_groups` ∩ `student.group_ids` — no direct `teacher_students` table.
- **Multi-group students**: a student can belong to multiple groups; billing aggregates across all per period.
- **SHORT/FULL tiers** (`StudentGroupTier`): only for kindergarten groups (ЮБ/БП); all others have one price.
- **Group billing modes**: `NONE` (free, attendance not billed), `PER_VISIT` (each attended lesson billed at group price), `SUBSCRIPTION` (not implemented).
- **NONE groups auto-save**: recording a group lesson for NONE-mode group skips attendance and saves immediately with `attendees=None`.
- **Duplicate guard**: `LessonService.create` blocks same teacher+group+date twice.
- **Client vs Student**: student attends lessons; client (parent) pays. Separate entities: `student.client_id` → `Client`; `student.parent_tg_ids` → list of Telegram IDs with bot access.
- **parent_tg_ids separator**: uses `|` (pipe), NOT `,` (comma). Russian-locale Google Sheets interprets comma as decimal separator. Parser accepts both for backwards compatibility (`student_repo.py`).
- **Client registration**: first-time registration is direct (no approval). Adding a second student requires admin approval via `client:add_child` FSM → `admin_child_ok` / `admin_child_no` callbacks (`admin/client_requests.py`).

## Special roles & configuration

**Клецова Ангелина (TCH-0002)** — assistant. Her menu has extra proxy buttons for Никишин (TCH-0005) and Криворчук (TCH-0008). Configured in `_PROXY_BUTTONS` in `bot/keyboards/teacher.py`. Proxy requests require admin approval (see Proxy approval above).

**PER_VISIT groups** (each attended lesson billed):
- GRP-0007, GRP-0008, GRP-0010, GRP-0015 — various groups
- GRP-0017 «БП Спортивная — Никишин» (TCH-0005): `price_full=700`
- GRP-0018 «БП Спортивная — Криворчук» (TCH-0008): `price_full=700`

## Environment

Required:
- `BOT_TOKEN` — Telegram bot token
- `GOOGLE_CREDENTIALS_JSON` — service account JSON (inline)
- `SPREADSHEET_ID` — main Google Spreadsheet ID

Optional — payments:
- `PAYMENT_CASH_ENABLED` — show cash payment option (default `True`)
- `PAYMENT_BANK_DETAILS` — bank details text (use `\n` for newlines; handler replaces `\\n` → `\n`)
- `PAYMENT_QR_DATA` — ЦБ РФ format string for QR generation (`ST00012|Name=...|PersonalAcc=...`)
- `PAYMENT_QR_IMAGE_URL` — fallback public HTTPS URL for QR image
- `PAYMENT_SBP_DETAILS` — SBP details text (phone / link)
- `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — YooKassa credentials
- `YOOKASSA_RETURN_URL` — return URL after YooKassa payment
- `PAYMENT_WEBHOOK_PORT` — port for YooKassa webhook server (default `8081`)

Optional — infrastructure:
- `WEBHOOK_URL` — if set, bot runs in webhook mode (recommended for production)
- `PORT` — HTTP port (default `8080`; Railway sets automatically)
- `REDIS_URL` — FSM state persistence across restarts (MemoryStorage if absent)

Production: Hetzner VPS, systemd (`fokus-bot.service`), deployed with `./scripts/deploy.sh`.
