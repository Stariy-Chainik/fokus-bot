"""Фейки для характеризующих тестов хендлеров: Telegram-объекты, FSM, репозитории, golden-снимки.

Golden-снимки лежат в tests/golden/<name>.txt. Отсутствующий файл создаётся при первом
прогоне (снимок текущего поведения), дальше тест сравнивает вывод с файлом байт-в-байт.
Обновить снимки после намеренного изменения экрана:

    UPDATE_GOLDEN=1 .venv/bin/python -m pytest -q

GOLDEN_STRICT=1 запрещает создавать отсутствующие снимки (для CI).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from bot.models import (
    Branch, Client, Group, Lesson, Student, StudentGroup, StudentPeriodPayment, Teacher,
    TeacherGroup, TeacherPeriodSubmission, User,
    TrainingEntry, AthleteTask,
)
from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus, StudentGroupTier
from bot.utils.attendees import attendee_ids

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def run(coro):
    return asyncio.run(coro)


# ─── Telegram-объекты ─────────────────────────────────────────────────────────

class FakeUser:
    def __init__(self, id: int = 1, full_name: str = "Тестов Тест") -> None:  # noqa: A002 — как в aiogram
        self.id = id
        self.full_name = full_name


class FakeMessage:
    """Message: запоминает всё, что хендлер показал (edit_text и answer — по порядку)."""

    def __init__(self, text: str | None = None, user_id: int = 1, message_id: int = 10) -> None:
        self.text = text
        self.message_id = message_id
        self.from_user = FakeUser(user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.screens: list[tuple[str, object]] = []
        self.photos: list[tuple[str | None, object]] = []
        self.markup_edits: list = []
        self.deleted = False

    async def edit_text(self, text, reply_markup=None, **_):
        self.screens.append((text, reply_markup))
        return self

    async def answer(self, text, reply_markup=None, **_):
        self.screens.append((text, reply_markup))
        return self

    async def answer_photo(self, photo, caption=None, reply_markup=None, **_):
        self.photos.append((caption, reply_markup))
        return self

    async def edit_reply_markup(self, reply_markup=None, **_):
        self.markup_edits.append(reply_markup)
        return self

    async def delete(self):
        self.deleted = True
        return True

    @property
    def html_text(self) -> str:
        return self.text or ""

    @property
    def last(self) -> tuple[str, object]:
        return self.screens[-1]


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, reply_markup=None, **_):
        self.sent.append((chat_id, text, reply_markup))

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_):
        self.sent.append((chat_id, caption, reply_markup))


class FakeCallbackQuery:
    def __init__(self, data: str = "", user_id: int = 1, message: FakeMessage | None = None, bot=None) -> None:
        self.id = "cbq-1"
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = message or FakeMessage(user_id=user_id)
        self.bot = bot or FakeBot()
        self.alerts: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, show_alert=False, **_):
        self.alerts.append((text, show_alert))


class FakeState:
    """FSMContext: get_data / update_data / set_state / clear."""

    def __init__(self, data=None, state=None) -> None:
        self._data = dict(data or {})
        self._state = state

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, data=None, **kwargs):
        self._data.update(data or {})
        self._data.update(kwargs)
        return dict(self._data)

    async def set_data(self, data):
        self._data = dict(data)

    async def set_state(self, state=None):
        self._state = state

    async def get_state(self):
        return self._state

    async def clear(self):
        self._data = {}
        self._state = None


def install_fake_show_card(monkeypatch, *modules: str) -> None:
    """Подменяет show_card (delete + answer снизу) в модулях-хендлерах.

    Настоящий show_card различает CallbackQuery по isinstance, что с фейками не работает;
    подмена повторяет его семантику: экран попадает в message.screens, сообщение помечается
    удалённым, callback получает answer().
    """
    async def _show_card(event, text, reply_markup=None):
        target = event.message if isinstance(event, FakeCallbackQuery) else event
        target.deleted = True
        target.screens.append((text, reply_markup))
        if isinstance(event, FakeCallbackQuery):
            event.alerts.append((None, False))

    for module in modules:
        monkeypatch.setattr(f"{module}.show_card", _show_card)


# ─── Репозитории ──────────────────────────────────────────────────────────────

class ByIdRepo:
    """get_all / get_by_id по атрибуту-ключу (teacher / group / branch / client).

    Возвращает копии, чтобы мутации в хендлерах не текли между тестами. Для групп
    поддерживает archived / include_archived как настоящий GroupRepository.
    """

    def __init__(self, items, key: str) -> None:
        self.items = list(items)
        self.key = key

    async def get_all(self, include_archived: bool = False):
        out = [replace(x) for x in self.items]
        if not include_archived:
            out = [x for x in out if not getattr(x, "archived", False)]
        return out

    async def get_by_id(self, id_):
        return next((replace(x) for x in self.items if getattr(x, self.key) == id_), None)

    async def get_by_branch(self, branch_id, include_archived: bool = False):
        return [g for g in await self.get_all(include_archived) if g.branch_id == branch_id]


class StudentRepoFake(ByIdRepo):
    def __init__(self, students) -> None:
        super().__init__(students, "student_id")

    async def get_by_parent_tg_id(self, tg_id):
        return [replace(s) for s in self.items if tg_id in s.parent_tg_ids]

    async def get_by_athlete_tg_id(self, tg_id):
        return next((replace(s) for s in self.items if s.athlete_tg_id == tg_id), None)


class ClientRepoFake(ByIdRepo):
    def __init__(self, clients) -> None:
        super().__init__(clients, "client_id")

    async def get_by_tg_id(self, tg_id):
        return next((replace(c) for c in self.items if c.tg_id == tg_id), None)


class LessonRepoFake:
    def __init__(self, lessons=()) -> None:
        self.items = list(lessons)
        self.deleted: list[str] = []
        self.attendees_updates: list[tuple[str, str]] = []

    async def get_all(self):
        return list(self.items)

    async def get_by_id(self, lesson_id):
        return next((x for x in self.items if x.lesson_id == lesson_id), None)

    async def get_by_teacher(self, teacher_id):
        return [x for x in self.items if x.teacher_id == teacher_id]

    async def get_by_teacher_and_period(self, teacher_id, period):
        return [x for x in self.items if x.teacher_id == teacher_id and x.date.startswith(period)]  # месяц или день, как в репо

    async def get_by_student_and_period(self, student_id, period):
        out = []
        for x in self.items:
            if x.date[:7] != period:
                continue
            slots = (x.student_1_id, x.student_2_id, x.student_3_id, x.student_4_id)
            if student_id in slots or student_id in attendee_ids(x.attendees or ""):
                out.append(x)
        return out

    async def get_existing_ids(self):
        return [x.lesson_id for x in self.items]

    async def individual_lesson_exists(self, teacher_id, student_id, lesson_date):
        for ls in self.items:
            if ls.teacher_id != teacher_id or ls.date != lesson_date:
                continue
            sids = [x for x in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id) if x]
            if len(sids) == 1 and sids[0] == student_id:
                return True
        return False

    async def add(self, lesson):
        self.items.append(lesson)
        return lesson

    async def delete(self, lesson_id):
        before = len(self.items)
        self.items = [x for x in self.items if x.lesson_id != lesson_id]
        removed = len(self.items) < before
        if removed:
            self.deleted.append(lesson_id)
        return removed

    async def update_attendees(self, lesson_id, attendees):
        self.attendees_updates.append((lesson_id, attendees))
        for x in self.items:
            if x.lesson_id == lesson_id:
                x.attendees = attendees
                return True
        return False


class PaymentRepoFake:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.added: list = []
        self.updated: list[tuple[str, int]] = []
        self.confirmed: list[tuple] = []

    async def get_all(self):
        return list(self.rows)

    async def get_by_id(self, payment_id):
        return next((r for r in self.rows if r.payment_id == payment_id), None)

    async def get_by_student_and_period(self, student_id, period):
        return [r for r in self.rows if r.student_id == student_id and r.period_month == period]

    async def get_rows_for(self, student_id, period, teacher_id):
        return [r for r in await self.get_by_student_and_period(student_id, period) if r.teacher_id == teacher_id]

    async def get_by_student_period_teacher(self, student_id, period, teacher_id):
        """Как настоящий репозиторий: только строка-остаток (не PAID)."""
        return next((r for r in await self.get_rows_for(student_id, period, teacher_id)
                     if r.status != PaymentStatus.PAID), None)

    async def get_existing_ids(self):
        return [r.payment_id for r in self.rows]

    async def add(self, payment):
        self.rows.append(payment)
        self.added.append(payment)
        return payment

    async def set_lesson_ids(self, payment_id, lesson_ids):
        for r in self.rows:
            if r.payment_id == payment_id:
                r.lesson_ids = lesson_ids
                return True
        return False

    async def update_amount(self, payment_id, amount):
        self.updated.append((payment_id, amount))
        for r in self.rows:
            if r.payment_id == payment_id:
                r.total_amount = amount
        return True

    async def confirm(self, payment_id, confirmed_by_tg_id, payment_method="admin_manual"):
        for r in self.rows:
            if r.payment_id == payment_id:
                r.status = PaymentStatus.PAID
                r.confirmed_by_tg_id = confirmed_by_tg_id
                r.payment_method = payment_method
                self.confirmed.append((payment_id, confirmed_by_tg_id, payment_method))
                return True
        return False

    async def confirm_all_for_period(self, student_id, period, confirmed_by_tg_id, payment_method="admin_manual"):
        count = 0
        for r in self.rows:
            if (r.student_id != student_id or r.period_month != period
                    or r.status == PaymentStatus.PAID or r.total_amount <= 0):
                continue
            r.status = PaymentStatus.PAID
            r.confirmed_by_tg_id = confirmed_by_tg_id
            r.payment_method = payment_method
            self.confirmed.append((r.payment_id, confirmed_by_tg_id, payment_method))
            count += 1
        return count


class SubmissionRepoFake:
    def __init__(self, subs=()) -> None:
        self.items = list(subs)
        self.deleted: list[tuple[str, str]] = []

    async def get_all(self):
        return list(self.items)

    async def get_by_teacher(self, teacher_id):
        return [s for s in self.items if s.teacher_id == teacher_id]

    async def get_by_teacher_and_period(self, teacher_id, period):
        return next((s for s in self.items if s.teacher_id == teacher_id and s.period_month == period), None)

    async def get_existing_ids(self):
        return [s.submission_id for s in self.items]

    async def add(self, sub):
        self.items.append(sub)
        return sub

    async def delete_by_teacher_and_period(self, teacher_id, period):
        before = len(self.items)
        self.items = [s for s in self.items if not (s.teacher_id == teacher_id and s.period_month == period)]
        if len(self.items) < before:
            self.deleted.append((teacher_id, period))
            return True
        return False


class TeacherGroupRepoFake:
    def __init__(self, teacher_to_groups: dict[str, list[str]]) -> None:
        self._t2g = {k: list(v) for k, v in teacher_to_groups.items()}

    async def get_all(self):
        return [TeacherGroup(tid, gid) for tid, gids in self._t2g.items() for gid in gids]

    async def get_groups_for_teacher(self, teacher_id):
        return list(self._t2g.get(teacher_id, []))

    async def get_teachers_for_group(self, group_id):
        return [tid for tid, gids in self._t2g.items() if group_id in gids]

    async def exists(self, teacher_id, group_id):
        return group_id in self._t2g.get(teacher_id, [])


class StudentGroupRepoFake:
    def __init__(self, rows=()) -> None:
        self.rows = [r if isinstance(r, StudentGroup) else StudentGroup(*r) for r in rows]
        self.removed: list[tuple[str, str]] = []

    async def get_all(self):
        return list(self.rows)

    async def get_groups_for_student(self, student_id, include_left: bool = False):
        return [r.group_id for r in self.rows if r.student_id == student_id and (include_left or r.is_active)]

    async def get_students_for_group(self, group_id, include_left: bool = False):
        return [r.student_id for r in self.rows if r.group_id == group_id and (include_left or r.is_active)]

    async def get_map_by_student(self):
        out: dict[str, list[str]] = {}
        for r in self.rows:
            if r.is_active:
                out.setdefault(r.student_id, []).append(r.group_id)
        return out

    async def get_membership_map(self):
        return {(r.student_id, r.group_id): r for r in self.rows}

    async def exists(self, student_id, group_id):
        return any(r.student_id == student_id and r.group_id == group_id for r in self.rows)

    async def add(self, student_id, group_id, joined_period=None):
        self.rows.append(StudentGroup(student_id, group_id, joined_period or ""))

    async def remove(self, student_id, group_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not (r.student_id == student_id and r.group_id == group_id)]
        if len(self.rows) < before:
            self.removed.append((student_id, group_id))
            return True
        return False

    async def remove_all_for_student(self, student_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.student_id != student_id]
        return before - len(self.rows)

    async def set_joined_period(self, student_id, group_id, joined_period):
        for r in self.rows:
            if r.student_id == student_id and r.group_id == group_id:
                r.joined_period = joined_period
                return True
        return False

    async def set_left_period(self, student_id, group_id, left_period):
        for r in self.rows:
            if r.student_id == student_id and r.group_id == group_id:
                r.left_period = left_period
                return True
        return False


class UserRepoFake:
    def __init__(self, users=()) -> None:
        self.items = list(users)

    async def get_all(self):
        return list(self.items)

    async def get_by_tg_id(self, tg_id):
        return next((u for u in self.items if u.tg_id == tg_id), None)

    async def get_admins(self):
        return [u for u in self.items if u.is_admin]


class OverrideRepoFake:
    """SalaryOverrideRepository: строки (teacher_id, date, minutes, comment)."""

    def __init__(self, rows=()) -> None:
        self.items = [SimpleNamespace(teacher_id=t, date=d, minutes=m, comment=c) for t, d, m, c in rows]

    async def get_for_teacher_period(self, teacher_id, period):
        return [o for o in self.items if o.teacher_id == teacher_id and o.date[:7] == period]


# ─── Конструкторы сущностей ───────────────────────────────────────────────────

def mk_user(tg_id: int = 1, is_admin: bool = False, teacher_id: str | None = None) -> User:
    return User(user_id=f"USR-{tg_id:04d}", tg_id=tg_id, is_admin=is_admin, teacher_id=teacher_id)


def mk_teacher(teacher_id="TCH-0001", name="Река Станислав", rate_group=1000, rate_for_teacher=1500,
               rate_for_student=2000, tg_id=None) -> Teacher:
    return Teacher(teacher_id, tg_id, name, rate_group, rate_for_teacher, rate_for_student)


def mk_student(student_id="STU-0001", name="Иванов Иван", **kwargs) -> Student:
    return Student(student_id, name, **kwargs)


def mk_group(group_id="GRP-0001", name="БП БТ Спортивная", branch_id="BRN-0001",
             billing_mode=GroupBillingMode.NONE, price_full=0, price_short=0,
             duration_short=35, duration_full=60, archived=False, sort_order=0) -> Group:
    return Group(group_id=group_id, branch_id=branch_id, name=name, sort_order=sort_order,
                 billing_mode=billing_mode, price_short=price_short, duration_short=duration_short,
                 price_full=price_full, duration_full=duration_full, archived=archived)


def mk_branch(branch_id="BRN-0001", name="Бутово Парк") -> Branch:
    return Branch(branch_id, name)


def mk_client(client_id="CLT-0001", name="Иванова Мария", tg_id=None, phone=None, email=None) -> Client:
    return Client(client_id, name, tg_id=tg_id, phone=phone, email=email)


def mk_lesson(lesson_id: str, teacher: Teacher, date: str, duration: int = 45,
              lesson_type: LessonType = LessonType.INDIVIDUAL, students=(), attendees=None,
              group_id: str = "") -> Lesson:
    """students — [(student_id, name), ...] в слоты 1..4 по порядку."""
    slots = list(students) + [(None, None)] * (4 - len(students))
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher.teacher_id, teacher_name=teacher.name,
        type=lesson_type,
        student_1_id=slots[0][0], student_1_name=slots[0][1],
        student_2_id=slots[1][0], student_2_name=slots[1][1],
        date=date, duration_min=duration, earned=0,
        recorded_at=f"{date} 10:00:00", updated_at=f"{date} 10:00:00",
        attendees=attendees, group_id=group_id,
        student_3_id=slots[2][0], student_3_name=slots[2][1],
        student_4_id=slots[3][0], student_4_name=slots[3][1],
    )


def mk_payment(payment_id: str, student_id: str, period: str, teacher_id: str, amount: int,
               status: PaymentStatus = PaymentStatus.PENDING, teacher_name: str = "",
               paid_at: str | None = None, method: str = "", confirmed_by=None, comment=None,
               student_name: str = "Иванов Иван") -> StudentPeriodPayment:
    return StudentPeriodPayment(
        payment_id=payment_id, student_id=student_id, student_name=student_name, period_month=period,
        total_amount=amount, status=status, paid_at=paid_at, confirmed_by_tg_id=confirmed_by,
        comment=comment, created_at="2026-09-01 10:00:00", updated_at="2026-09-01 10:00:00",
        teacher_id=teacher_id, teacher_name=teacher_name, payment_method=method,
    )


def mk_submission(teacher_id: str, period: str, lessons_count: int = 0, total_earned: int = 0) -> TeacherPeriodSubmission:
    return TeacherPeriodSubmission(
        submission_id=f"SUB-{teacher_id[-4:]}-{period}", teacher_id=teacher_id, period_month=period,
        submitted_at=f"{period}-26 12:00:00", lessons_count=lessons_count, total_earned=total_earned,
    )


SHORT = StudentGroupTier.SHORT


# ─── Golden-снимки ───────────────────────────────────────────────────────────

def markup_dump(markup) -> str:
    """Клавиатура строками: `[текст] → callback` через два пробела в ряду."""
    if markup is None:
        return "(нет клавиатуры)"
    rows = []
    for row in markup.inline_keyboard:
        cells = []
        for b in row:
            target = b.callback_data if b.callback_data is not None else f"url={b.url}"
            cells.append(f"[{b.text}] → {target}")
        rows.append("  ".join(cells))
    return "\n".join(rows)


def screen_dump(text: str, markup=None) -> str:
    return f"{text}\n--- клавиатура ---\n{markup_dump(markup)}\n"


def assert_golden(name: str, content: str) -> None:
    """Сравнивает content с tests/golden/<name>.txt; отсутствующий снимок создаётся."""
    GOLDEN_DIR.mkdir(exist_ok=True)
    path = GOLDEN_DIR / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        if not path.exists() and os.environ.get("GOLDEN_STRICT"):
            raise AssertionError(f"Нет golden-снимка {path.name} (GOLDEN_STRICT)")
        path.write_text(content, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert content == expected, (
        f"Экран отличается от снимка {path.name}.\n--- снимок ---\n{expected}\n--- сейчас ---\n{content}"
    )


# ─── Google Sheets в памяти (для тестов репозиториев) ────────────────────────

class FakeWorksheet:
    """gspread.Worksheet: строка 1 — заголовки, данные с 2; значения хранятся как переданы."""

    def __init__(self, headers: list[str], rows: list[list]) -> None:
        self.headers = list(headers)
        self.rows = [list(r) for r in rows]
        self.calls: list[tuple] = []

    def _pad(self, row: list) -> list:
        return list(row) + [None] * (len(self.headers) - len(row))

    def get_all_records(self, default_blank=None):
        self.calls.append(("get_all_records",))
        return [{h: (v if v not in (None, "") else default_blank) for h, v in zip(self.headers, self._pad(r), strict=False)}
                for r in self.rows]

    def row_values(self, index: int):
        self.calls.append(("row_values", index))
        if index == 1:
            return list(self.headers)
        if index - 2 >= len(self.rows):
            return []
        return ["" if v is None else str(v) for v in self.rows[index - 2]]

    def update_cell(self, row: int, col: int, value):
        self.calls.append(("update_cell", row, col, value))
        padded = self._pad(self.rows[row - 2])
        padded[col - 1] = value
        self.rows[row - 2] = padded

    def update(self, range_name: str, values):
        self.calls.append(("update", range_name))
        self.rows[int(range_name[1:]) - 2] = list(values[0])

    def delete_rows(self, index: int):
        self.calls.append(("delete_rows", index))
        del self.rows[index - 2]

    def append_row(self, values, value_input_option=None):
        self.calls.append(("append_row", list(values)))
        self.rows.append(list(values))

    def insert_row(self, values, index: int):
        """Внешняя правка листа (руками / скриптом) — кеш репозитория об этом не знает."""
        self.rows.insert(index - 2, list(values))

    def by_key(self, col: str, value) -> list | None:
        i = self.headers.index(col)
        return next((self._pad(r) for r in self.rows if str(r[i]) == str(value)), None)


class FakeSheetsClient:
    def __init__(self, ws: FakeWorksheet) -> None:
        self.ws = ws

    def get_worksheet(self, sheet_name: str) -> FakeWorksheet:
        return self.ws


# ─── Фейки с записью (API кабинета администратора) ────────────────────────────

class GroupRepoFake(ByIdRepo):
    def __init__(self, groups) -> None:
        super().__init__(groups, "group_id")

    def _find(self, gid):
        return next((g for g in self.items if g.group_id == gid), None)

    async def add(self, branch_id, name):
        g = Group(group_id=f"GRP-{len(self.items) + 1:04d}", branch_id=branch_id, name=name)
        self.items.append(g)
        return replace(g)

    async def update_name(self, gid, name):
        g = self._find(gid)
        if g: g.name = name  # noqa: E701
        return g is not None

    async def update_billing(self, gid, billing_mode, price_short, duration_short, price_full, duration_full):
        g = self._find(gid)
        if g is None:
            return False
        g.billing_mode, g.price_full, g.duration_full = billing_mode, price_full, duration_full
        g.price_short, g.duration_short = price_short, duration_short
        return True

    async def set_archived(self, gid, archived):
        g = self._find(gid)
        if g: g.archived = archived  # noqa: E701
        return g is not None

    async def delete(self, gid):
        before = len(self.items)
        self.items = [g for g in self.items if g.group_id != gid]
        return len(self.items) < before


class BranchRepoFake(ByIdRepo):
    def __init__(self, branches) -> None:
        super().__init__(branches, "branch_id")

    async def add(self, name):
        b = Branch(branch_id=f"BRN-{len(self.items) + 1:04d}", name=name)
        self.items.append(b)
        return replace(b)

    async def update_name(self, bid, name):
        b = next((x for x in self.items if x.branch_id == bid), None)
        if b: b.name = name  # noqa: E701
        return b is not None

    async def delete(self, bid):
        before = len(self.items)
        self.items = [b for b in self.items if b.branch_id != bid]
        return len(self.items) < before


class TeacherRepoFake(ByIdRepo):
    def __init__(self, teachers) -> None:
        super().__init__(teachers, "teacher_id")

    async def add(self, tg_id, name, rate_group, rate_for_teacher, rate_for_student):
        t = Teacher(teacher_id=f"TCH-{len(self.items) + 1:04d}", tg_id=tg_id, name=name,
                    rate_group=rate_group, rate_for_teacher=rate_for_teacher, rate_for_student=rate_for_student)
        self.items.append(t)
        return replace(t)

    async def update_rates(self, tid, rate_group, rate_for_teacher, rate_for_student):
        t = next((x for x in self.items if x.teacher_id == tid), None)
        if t is None:
            return False
        t.rate_group, t.rate_for_teacher, t.rate_for_student = rate_group, rate_for_teacher, rate_for_student
        return True

    async def delete(self, tid):
        before = len(self.items)
        self.items = [t for t in self.items if t.teacher_id != tid]
        return len(self.items) < before


class StudentRepoWritable(StudentRepoFake):
    def _find(self, sid):
        return next((s for s in self.items if s.student_id == sid), None)

    async def add(self, name):
        s = Student(student_id=f"STU-{len(self.items) + 1:04d}", name=name)
        self.items.append(s)
        return replace(s)

    async def update_name(self, sid, name):
        s = self._find(sid)
        if s: s.name = name  # noqa: E701
        return s is not None

    async def update_tier(self, sid, tier):
        s = self._find(sid)
        if s: s.group_tier = tier  # noqa: E701
        return s is not None

    async def delete(self, sid):
        before = len(self.items)
        self.items = [s for s in self.items if s.student_id != sid]
        return len(self.items) < before

    async def set_partner(self, sid, pid):
        if sid == pid:
            raise ValueError("сам себе")
        a, b = self._find(sid), self._find(pid)
        for x in (a, b):
            if x.partner_id and x.partner_id not in (sid, pid):
                old = self._find(x.partner_id)
                if old: old.partner_id = None  # noqa: E701
        a.partner_id, b.partner_id = pid, sid

    async def clear_partner(self, sid):
        s = self._find(sid)
        if s and s.partner_id:
            p = self._find(s.partner_id)
            if p: p.partner_id = None  # noqa: E701
            s.partner_id = None

    async def set_client_id(self, sid, cid):
        s = self._find(sid)
        if s: s.client_id = cid  # noqa: E701
        return s is not None

    async def set_athlete_tg_id(self, sid, tg_id):
        s = self._find(sid)
        if s: s.athlete_tg_id = tg_id  # noqa: E701
        return s is not None


class ClientRepoWritable(ClientRepoFake):
    async def create(self, name, created_by_tg_id, phone="", tg_id=None, max_id=None):
        c = Client(client_id=f"CLT-{len(self.items) + 1:04d}", name=name, tg_id=tg_id, phone=phone or None, max_id=max_id)
        self.items.append(c)
        return replace(c)


class StudentGroupRepoWritable(StudentGroupRepoFake):
    async def remove_all_for_group(self, group_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.group_id != group_id]
        return before - len(self.rows)


class TeacherGroupRepoWritable(TeacherGroupRepoFake):
    async def add(self, teacher_id, group_id):
        self._t2g.setdefault(teacher_id, []).append(group_id)
        return TeacherGroup(teacher_id, group_id)

    async def remove(self, teacher_id, group_id):
        gids = self._t2g.get(teacher_id, [])
        if group_id in gids:
            gids.remove(group_id)
            return True
        return False

    async def remove_all_for_group(self, group_id):
        n = 0
        for gids in self._t2g.values():
            while group_id in gids:
                gids.remove(group_id); n += 1  # noqa: E702
        return n

    async def remove_all_for_teacher(self, teacher_id):
        return len(self._t2g.pop(teacher_id, []))


class UserRepoWritable(UserRepoFake):
    async def add(self, tg_id, teacher_id=None, is_admin=False):
        u = User(user_id=f"USR-{len(self.items) + 1:04d}", tg_id=tg_id, is_admin=is_admin, teacher_id=teacher_id)
        self.items.append(u)
        return u

    async def update_teacher_id(self, tg_id, teacher_id):
        u = await self.get_by_tg_id(tg_id)
        if u: u.teacher_id = teacher_id  # noqa: E701
        return u is not None

    async def delete_by_teacher_id(self, teacher_id):
        before = len(self.items)
        self.items = [u for u in self.items if u.teacher_id != teacher_id]
        return len(self.items) < before


class SubOverrideRepoFake:
    """SubscriptionOverrideRepository: (group_id, period|'*', student_id|None) → amount."""

    def __init__(self, rows=()) -> None:
        from bot.models.entities import SubscriptionOverride
        self._cls = SubscriptionOverride
        self.items = [r if isinstance(r, SubscriptionOverride) else SubscriptionOverride(*r) for r in rows]

    async def get_all(self):
        return list(self.items)

    async def get_for_group(self, group_id):
        return [o for o in self.items if o.group_id == group_id]

    async def upsert(self, group_id, period_month, student_id, amount):
        sid = student_id or None
        self.items = [o for o in self.items if (o.group_id, o.period_month, o.student_id) != (group_id, period_month, sid)]
        o = self._cls(group_id=group_id, period_month=period_month, student_id=sid, amount=amount)
        self.items.append(o)
        return o

    async def delete(self, group_id, period_month, student_id):
        sid = student_id or None
        before = len(self.items)
        self.items = [o for o in self.items if (o.group_id, o.period_month, o.student_id) != (group_id, period_month, sid)]
        return len(self.items) < before


class TrainingEntryRepoFake:
    """Дневник спортсмена в памяти: записи и оценки (bot/repositories/training_entry_repo.py)."""

    def __init__(self, entries=()) -> None:
        self.items = list(entries)

    async def get_all(self):
        return list(self.items)

    async def get_by_id(self, entry_id):
        return next((e for e in self.items if e.entry_id == entry_id), None)

    async def get_for_student(self, student_id):
        return [e for e in self.items if e.student_id == student_id]

    async def add(self, student_id, date, minutes, topics, task_ids, comment=""):
        entry = TrainingEntry(entry_id=f"TE-{len(self.items) + 1:06d}", student_id=student_id, date=date,
                              minutes=minutes, topics=list(topics), task_ids=list(task_ids), comment=comment,
                              created_at=f"{date} 12:00:00")
        self.items.append(entry)
        return entry

    async def set_grade(self, entry_id, grade, grade_comment, graded_by):
        entry = await self.get_by_id(entry_id)
        if entry is None:
            return None
        entry.grade, entry.grade_comment, entry.graded_by = grade, grade_comment, graded_by
        return entry

    async def delete(self, entry_id):
        before = len(self.items)
        self.items = [e for e in self.items if e.entry_id != entry_id]
        return len(self.items) < before


class AthleteTaskRepoFake:
    """Задания педагога спортсмену в памяти (bot/repositories/athlete_task_repo.py)."""

    def __init__(self, tasks=()) -> None:
        self.items = list(tasks)

    async def get_all(self):
        return list(self.items)

    async def get_by_id(self, task_id):
        return next((t for t in self.items if t.task_id == task_id), None)

    async def get_for_student(self, student_id, only_open=False):
        return [t for t in self.items if t.student_id == student_id and (not only_open or t.status == "open")]

    async def add(self, student_id, teacher_id, exercise, minutes, comment="", source="teacher"):
        task = AthleteTask(task_id=f"TK-{len(self.items) + 1:06d}", student_id=student_id, teacher_id=teacher_id,
                           exercise=exercise, minutes=minutes, comment=comment, source=source, created_at="2026-09-01 12:00:00")
        self.items.append(task)
        return task

    async def close(self, task_id):
        task = await self.get_by_id(task_id)
        if task is not None:
            task.status, task.closed_at = "closed", "2026-09-20 12:00:00"
        return task


def mk_entry(entry_id: str, student_id: str, date: str, minutes: int = 60,
             topics=(), grade=None, comment: str = "", task_ids=()) -> TrainingEntry:
    return TrainingEntry(entry_id=entry_id, student_id=student_id, date=date, minutes=minutes,
                         topics=list(topics), task_ids=list(task_ids), comment=comment,
                         created_at=f"{date} 12:00:00", grade=grade)
