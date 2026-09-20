"""Накопительный счёт: оплаты складываются, галочки по порядку дат, остаток синхронизируется."""
import asyncio
from types import SimpleNamespace

from bot.models import StudentPeriodPayment, Student
from bot.models.enums import PaymentStatus
from bot.services.payment_ledger import (
    BillAggregate, lesson_marks, lesson_paid_marks, paid_lesson_ids, paid_sums, TeacherLedger, ledger_totals,
)
from bot.services.payment_service import PaymentService


def test_lesson_paid_marks_cumulative():
    assert lesson_paid_marks([1300, 1300, 1300], 2600) == [True, True, False]
    assert lesson_paid_marks([1300, 1300], 0) == [False, False]
    assert lesson_paid_marks([800, 2000], 2800) == [True, True]
    assert lesson_paid_marks([800, 2000], 2799) == [True, False]
    assert lesson_paid_marks([], 500) == []


def test_paid_sums_ignores_pending():
    rows = [SimpleNamespace(teacher_id="T1", status=PaymentStatus.PAID, total_amount=1000),
            SimpleNamespace(teacher_id="T1", status=PaymentStatus.PAID, total_amount=500),
            SimpleNamespace(teacher_id="T1", status=PaymentStatus.PENDING, total_amount=300),
            SimpleNamespace(teacher_id="T2", status="paid", total_amount=7000)]
    assert paid_sums(rows) == {"T1": 1500, "T2": 7000}


def test_teacher_ledger_properties():
    row = SimpleNamespace(payment_id="PAY-000042")
    ledger = TeacherLedger("T1", "Река", accrued=3900, paid=2600, pending=row)
    assert (ledger.remainder, ledger.overpaid, ledger.fully_paid, ledger.pending_pid) == (1300, 0, False, 42)
    l2 = TeacherLedger("T1", "Река", accrued=1000, paid=1200)
    assert (l2.remainder, l2.overpaid, l2.fully_paid, l2.pending_pid) == (0, 200, True, 0)
    assert ledger_totals({"a": ledger, "b": l2}) == (4900, 3800, 1300)


# ── ledger_for: синхронизация строки-остатка ─────────────────────────────────

def _row(pid, tid, amount, status, sid="STU-1", period="2026-09"):
    return StudentPeriodPayment(
        payment_id=pid, student_id=sid, student_name="Иванов", period_month=period,
        total_amount=amount, status=status, paid_at="2026-09-08" if status == PaymentStatus.PAID else None,
        confirmed_by_tg_id=None, comment=None, created_at="", updated_at="", teacher_id=tid, teacher_name="Река",
    )


class _PayRepo:
    def __init__(self, rows):
        self.rows, self.updated, self.added = list(rows), [], []

    async def get_all(self):
        return list(self.rows)

    async def get_by_student_and_period(self, sid, period):
        return [r for r in self.rows if r.student_id == sid and r.period_month == period]

    async def get_existing_ids(self):
        return [r.payment_id for r in self.rows]

    async def add(self, p):
        self.rows.append(p)
        self.added.append(p)
        return p

    async def update_amount(self, pid, amount):
        self.updated.append((pid, amount))
        for r in self.rows:
            if r.payment_id == pid:
                r.total_amount = amount
        return True


def _service(rows, bills):
    svc = PaymentService(_PayRepo(rows), lesson_repo=None, teacher_repo=None)

    async def _bills(sid, period):
        return bills
    svc.compute_bills_for_student_period = _bills
    return svc


def _student():
    return Student("STU-1", "Иванов")


def test_ledger_creates_remainder_after_partial_payment():
    bills = {"T1": BillAggregate("Река", 3900)}
    svc = _service([_row("PAY-000001", "T1", 2600, PaymentStatus.PAID)], bills)
    ledgers = asyncio.run(svc.ledger_for(_student(), "2026-09"))
    ledger = ledgers["T1"]
    assert (ledger.paid, ledger.remainder) == (2600, 1300)
    assert ledger.pending is not None and ledger.pending.total_amount == 1300 and ledger.pending.status == PaymentStatus.PENDING
    assert len(svc._payment_repo.added) == 1  # создана новая строка-остаток, оплаченная не тронута


def test_ledger_updates_existing_remainder_and_sums_many_payments():
    bills = {"T1": BillAggregate("Река", 5200)}
    rows = [_row("PAY-000001", "T1", 1300, PaymentStatus.PAID), _row("PAY-000002", "T1", 1300, PaymentStatus.PAID),
            _row("PAY-000003", "T1", 999, PaymentStatus.PENDING)]
    svc = _service(rows, bills)
    ledger = asyncio.run(svc.ledger_for(_student(), "2026-09"))["T1"]
    assert (ledger.paid, ledger.remainder, ledger.pending.payment_id, ledger.pending.total_amount) == (2600, 2600, "PAY-000003", 2600)
    assert svc._payment_repo.updated == [("PAY-000003", 2600)] and not svc._payment_repo.added


def test_ledger_fully_paid_sets_remainder_zero_and_no_new_rows():
    bills = {"T1": BillAggregate("Река", 2600)}
    svc = _service([_row("PAY-000001", "T1", 2600, PaymentStatus.PAID)], bills)
    ledger = asyncio.run(svc.ledger_for(_student(), "2026-09"))["T1"]
    assert ledger.fully_paid and ledger.pending is None and not svc._payment_repo.added
    # переплата после удаления урока
    bills["T1"].total = 2000
    ledger = asyncio.run(svc.ledger_for(_student(), "2026-09"))["T1"]
    assert (ledger.remainder, ledger.overpaid) == (0, 600)


def test_get_or_create_returns_paid_rows_and_remainder():
    bills = {
        "T1": BillAggregate("Река", 3000),
        "SUB:G": BillAggregate("Абонемент", 7000, subscription=True),
    }
    svc = _service([_row("PAY-000001", "T1", 1000, PaymentStatus.PAID)], bills)
    rows = asyncio.run(svc.get_or_create_invoices_for_student_period(_student(), "2026-09"))
    kinds = sorted((r.teacher_id, r.status.value, r.total_amount) for r in rows)
    assert kinds == [("SUB:G", "pending", 7000), ("T1", "paid", 1000), ("T1", "pending", 2000)]


# ── record_payment: зачёт суммы по остаткам ──────────────────────────────────

class _PayRepo2(_PayRepo):
    def __init__(self, rows):
        super().__init__(rows)
        self.confirmed = []

    async def confirm(self, pid, by, payment_method="admin_manual"):
        self.confirmed.append((pid, payment_method))
        for r in self.rows:
            if r.payment_id == pid:
                r.status = PaymentStatus.PAID
                r.payment_method = payment_method
        return True


def _service2(rows, bills):
    svc = PaymentService(_PayRepo2(rows), lesson_repo=None, teacher_repo=None)

    async def _bills(sid, period):
        return bills
    svc.compute_bills_for_student_period = _bills
    return svc


def _state(svc):
    return sorted((r.teacher_id, r.status.value, r.total_amount) for r in svc._payment_repo.rows)


def test_record_payment_full_remainder_confirms_pending_row():
    bills = {"T1": BillAggregate("Река", 2600)}
    svc = _service2([_row("PAY-000001", "T1", 2600, PaymentStatus.PENDING)], bills)
    assert asyncio.run(svc.record_payment("STU-1", "Иванов", "2026-09", 2600, 7)) == (2600, 1)
    assert svc._payment_repo.confirmed == [("PAY-000001", "admin_manual")]
    assert svc._payment_repo.rows[0].payment_method == "admin_manual"
    assert _state(svc) == [("T1", "paid", 2600)]


def test_record_payment_partial_splits_row_and_keeps_remainder():
    """Чек на 1300 при остатке 3900 (остаток вырос после новых уроков): зачтено 1300, остаток 2600."""
    bills = {"T1": BillAggregate("Река", 3900)}
    svc = _service2([_row("PAY-000001", "T1", 3900, PaymentStatus.PENDING)], bills)
    assert asyncio.run(svc.record_payment("STU-1", "Иванов", "2026-09", 1300, 7, comment="чек")) == (1300, 1)
    assert _state(svc) == [("T1", "paid", 1300), ("T1", "pending", 2600)]
    assert svc._payment_repo.confirmed == []
    assert svc._payment_repo.added[0].payment_method == "admin_manual"


def test_record_payment_allocates_in_teacher_order_and_overpays_to_first():
    bills = {
        "T1": BillAggregate("Река", 1000),
        "T2": BillAggregate("Абонемент", 7000, subscription=True),
    }
    svc = _service2([_row("PAY-000001", "T1", 1000, PaymentStatus.PENDING), _row("PAY-000002", "T2", 7000, PaymentStatus.PENDING)], bills)
    # выбраны оба, сумма 8500 → 1000 + 7000 закрыты целиком, 500 — переплата на первого (T1)
    credited, rows = asyncio.run(svc.record_payment("STU-1", "Иванов", "2026-09", 8500, 7, ["T1", "T2"]))
    assert (credited, rows) == (8500, 3)
    assert _state(svc) == [("T1", "paid", 500), ("T1", "paid", 1000), ("T2", "paid", 7000)]


def test_record_payment_only_selected_teachers():
    bills = {"T1": BillAggregate("Река", 1000), "T2": BillAggregate("Власов", 800)}
    svc = _service2([_row("PAY-000001", "T1", 1000, PaymentStatus.PENDING), _row("PAY-000002", "T2", 800, PaymentStatus.PENDING)], bills)
    assert asyncio.run(svc.record_payment("STU-1", "Иванов", "2026-09", 800, 7, ["T2"])) == (800, 1)
    assert _state(svc) == [("T1", "pending", 1000), ("T2", "paid", 800)]


def test_record_payment_nothing_pending_returns_zero():
    bills = {"T1": BillAggregate("Река", 1000)}
    svc = _service2([_row("PAY-000001", "T1", 1000, PaymentStatus.PAID)], bills)
    # всё оплачено, новый чек на 1000 → переплата отдельной строкой
    assert asyncio.run(svc.record_payment("STU-1", "Иванов", "2026-09", 1000, 7)) == (1000, 1)
    assert _state(svc) == [("T1", "paid", 1000), ("T1", "paid", 1000)]


# ── lesson_marks: занятия с отметками для экрана админа ──────────────────────

def _bill(lesson_id, date, amount, duration=45, lesson_type="individual"):
    return SimpleNamespace(lesson_id=lesson_id, date=date, amount=amount,
                           duration_min=duration, lesson_type=lesson_type)


def test_lesson_marks_orders_by_date_and_marks_cumulatively():
    from bot.services.payment_ledger import lesson_marks
    items = [_bill("LES-3", "2026-09-11", 1667, 60), _bill("LES-1", "2026-09-01", 2500, 90),
             _bill("LES-2", "2026-09-04", 2500, 90)]
    marks = lesson_marks(items, paid=5000)
    assert [(m["lesson_id"], m["paid"]) for m in marks] == [
        ("LES-1", True), ("LES-2", True), ("LES-3", False)]
    assert marks[0]["duration_min"] == 90 and marks[2]["amount"] == 1667


def test_lesson_marks_without_payments_all_unpaid():
    from bot.services.payment_ledger import lesson_marks
    marks = lesson_marks([_bill("LES-1", "2026-09-01", 800)], paid=0)
    assert marks == [{"lesson_id": "LES-1", "date": "2026-09-01", "duration_min": 45,
                      "amount": 800, "lesson_type": "individual", "paid": False}]


# ── mark_student_lessons: экран «Занятия» родителя ───────────────────────────

def test_mark_student_lessons_shares_and_cumulative_marks():
    from bot.models.enums import LessonType
    from bot.services.payment_ledger import mark_student_lessons
    from tests.fakes import mk_lesson, mk_payment, mk_teacher
    t1 = mk_teacher("TCH-0001", "Река", rate_for_student=2000)
    t2 = mk_teacher("TCH-0002", "Никишин", rate_for_student=3000)
    lessons = [
        mk_lesson("L3", t1, "2026-09-09", 60, students=[("STU-1", "A"), ("STU-2", "B")]),   # доля STU-1: 1334
        mk_lesson("L1", t1, "2026-09-02", 45, students=[("STU-1", "A")]),                     # 2000
        mk_lesson("L2", t2, "2026-09-05", 90, LessonType.GROUP, group_id="GRP-1", attendees="STU-1:90:800"),
        mk_lesson("L4", t2, "2026-09-07", 90, LessonType.GROUP, group_id="GRP-1", attendees=None),  # абонемент → 0
        mk_lesson("L5", mk_teacher("TCH-0404", "Нет"), "2026-09-08", 45, students=[("STU-1", "A")]),  # педагог не найден
    ]
    rows = [mk_payment("P1", "STU-1", "2026-09", "TCH-0001", 2000, PaymentStatus.PAID),
            mk_payment("P2", "STU-1", "2026-09", "TCH-0002", 100, PaymentStatus.PENDING)]
    month = mark_student_lessons("STU-1", lessons, {t.teacher_id: t for t in (t1, t2)}, rows)
    assert [ls.lesson_id for ls in month.lessons] == ["L1", "L2", "L4", "L5", "L3"]
    assert {k: (m.amount, m.paid) for k, m in month.marks.items()} == {
        "L1": (2000, True), "L3": (1334, False), "L2": (800, False), "L4": (0, False), "L5": (0, False)}
    assert month.mark("L404") == month.mark("L4")


def _item(lesson_id: str, date: str, amount: int):
    return SimpleNamespace(lesson_id=lesson_id, date=date, duration_min=45, amount=amount, lesson_type="individual")


def test_chosen_lessons_get_the_checkmark_not_the_earliest():
    """Плательщик выбрал занятия 3 и 4 — ✅ именно у них, а не у самых ранних."""
    items = [_item("LES-1", "2026-09-01", 1900), _item("LES-2", "2026-09-03", 1900),
             _item("LES-3", "2026-09-05", 1900), _item("LES-4", "2026-09-08", 1900)]
    marks = lesson_marks(items, paid=3800, linked_ids={"LES-3", "LES-4"})
    assert [(m["lesson_id"], m["paid"]) for m in marks] == [
        ("LES-1", False), ("LES-2", False), ("LES-3", True), ("LES-4", True)]
    # без выбора — прежнее поведение: закрываются самые ранние
    assert [m["paid"] for m in lesson_marks(items, paid=3800)] == [True, True, False, False]


def test_linked_and_unlinked_payments_live_together():
    """Одна оплата за выбранное занятие, вторая просто суммой — обе учитываются."""
    items = [_item("LES-1", "2026-09-01", 1000), _item("LES-2", "2026-09-03", 1000),
             _item("LES-3", "2026-09-05", 1000)]
    marks = lesson_marks(items, paid=2000, linked_ids={"LES-3"})
    assert [m["paid"] for m in marks] == [True, False, True]   # 1000 закрыли LES-3, остаток — с начала


def test_paid_lesson_ids_reads_only_paid_rows():
    rows = [SimpleNamespace(teacher_id="T1", status=PaymentStatus.PAID, lesson_id_list=["LES-1", "LES-2"]),
            SimpleNamespace(teacher_id="T1", status=PaymentStatus.PAID, lesson_id_list=[]),
            SimpleNamespace(teacher_id="T1", status=PaymentStatus.PENDING, lesson_id_list=["LES-9"]),
            SimpleNamespace(teacher_id="T2", status="paid", lesson_id_list=["LES-5"])]
    assert paid_lesson_ids(rows) == {"T1": {"LES-1", "LES-2"}, "T2": {"LES-5"}}
