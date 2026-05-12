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
    teachers.py          # teacher CRUD, rates, period submission status
    students.py          # student CRUD, partnerships, client linking
    client_requests.py   # approve/reject parent→student link requests
    salaries.py          # teacher earnings by period
    bills.py             # student invoices, send to parent
    profit.py            # school revenue (Выручка) vs salary summary
    branches.py          # branches, groups, teacher↔group assignment
    edit_lesson.py       # admin-only lesson edit/delete (bypasses period lock)
    diagnostics.py       # data integrity checks
    record_lesson.py     # admin proxy: record lessons on behalf of any teacher
  teacher/
    record_lesson.py     # FSM: record individual / pair / group lessons
    my_lessons.py        # view and delete own lessons
    submit_period.py     # lock period for billing
    my_groups.py         # group roster view
    partners.py          # manage pairs and soloists
  client/
    start.py             # client registration + add-child flow
    my_lessons.py        # lesson history for parent (with ✅ paid status)
    my_bills.py          # invoices by month + payment methods flow
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
| `PaymentService` | Invoice computation; `get_or_create_invoices_for_student_period()`; `confirm_period()` |
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
1. 💵 Наличные — admin gets notification with confirm button
2. 🏦 По реквизитам — shows QR + bank details; client uploads receipt
3. 📱 СБП — shows SBP details; client uploads receipt

Receipt upload uses FSM `ReceiptStates.waiting_for_receipt` (`bot/states/client_states.py`). On receipt: admins receive photo/document with «✅ Подтвердить оплату» button. Admin confirms → `confirm_period()` → all PENDING invoices for that (student, period) → PAID.

**Important**: if a lesson is added to a period after its invoice is already PAID, the new amount will NOT be automatically billed (confirm skips PAID records).

### FSM

States in `bot/states/`. Multi-step flows use aiogram FSM with explicit Back/Cancel at each step. State cleared on cancel or completion.

**Admin proxy pattern**: `proxy_teacher_id` in FSM state lets admin or Клецова record on behalf of another teacher. Helper `_tid(user, data)` returns `data.get("proxy_teacher_id") or user.teacher_id`.

**Proxy approval**: when Клецова (TCH-0002) initiates a proxy record, admins receive a confirmation request. After admin approves, Клецова gets a «▶ Начать запись» button. Admins bypass approval entirely. Handlers: `proxy_approve`, `proxy_deny`, `proxy_record_go` in `bot/handlers/teacher/record_lesson.py`.

### ID format

All primary keys are human-readable sequential strings:
`TCH-0001`, `STU-0042`, `LES-000123`, `GRP-0001`, `BRN-001`, etc.
Generated by `bot/utils/ids.py` which scans existing rows for the current max.

## Key domain rules

- **Period submission** (`TeacherPeriodSubmission`): once submitted, lessons are locked — no edits/deletes except by admin.
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
