"""Тесты PaymentService.compute_debt_map() — сводный расчёт долгов (экран «Должники»).

Правило: долг = начисления (build_billing_rows) минус PAID-оплаты по
(student, teacher, period); наличие счёта роли не играет; amount=0 — абонемент.
"""
import asyncio

from bot.models import Teacher, Lesson, StudentPeriodPayment
from bot.models.enums import LessonType, PaymentStatus
from bot.services.payment_service import PaymentService


def _run(coro):
    return asyncio.run(coro)


class _FakeLessonRepo:
    def __init__(self, lessons):
        self._lessons = lessons

    async def get_all(self):
        return self._lessons


class _FakeTeacherRepo:
    def __init__(self, teachers):
        self._teachers = teachers

    async def get_all(self):
        return self._teachers


class _FakePaymentRepo:
    def __init__(self, payments):
        self._payments = payments

    async def get_all(self):
        return self._payments


def _teacher(tid="TCH-0001", rate_for_student=1000):
    return Teacher(
        teacher_id=tid, tg_id=None, name=f"Педагог {tid}",
        rate_group=800, rate_for_teacher=900, rate_for_student=rate_for_student,
    )


def _lesson(lesson_id, teacher_id, lesson_date, *, s1=None, s2=None,
            lesson_type=LessonType.INDIVIDUAL, duration=45, attendees=None):
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher_id, teacher_name="—",
        type=lesson_type,
        student_1_id=s1, student_1_name=s1, student_2_id=s2, student_2_name=s2,
        date=lesson_date, duration_min=duration, earned=0,
        recorded_at="", updated_at="", attendees=attendees,
    )


def _payment(student_id, teacher_id, period, status):
    return StudentPeriodPayment(
        payment_id="PAY-000001", student_id=student_id, student_name="—",
        period_month=period, total_amount=0, status=status,
        paid_at=None, confirmed_by_tg_id=None, comment=None,
        created_at="", updated_at="", teacher_id=teacher_id, teacher_name="—",
    )


def _service(lessons, teachers, payments):
    return PaymentService(
        payment_repo=_FakePaymentRepo(payments),
        lesson_repo=_FakeLessonRepo(lessons),
        teacher_repo=_FakeTeacherRepo(teachers),
    )


def test_unpaid_pair_lesson_split_between_students():
    svc = _service(
        [_lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A", s2="STU-B")],
        [_teacher()], [],
    )
    debts = _run(svc.compute_debt_map())
    # base = 1000 × 45/45 = 1000, поровну по 500
    assert debts == {"STU-A": {"2026-05": 500}, "STU-B": {"2026-05": 500}}


def test_paid_period_excluded():
    svc = _service(
        [_lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A")],
        [_teacher()],
        [_payment("STU-A", "TCH-0001", "2026-05", PaymentStatus.PAID)],
    )
    assert _run(svc.compute_debt_map()) == {}


def test_pending_invoice_still_counts_as_debt():
    """PENDING-счёт долг не гасит — важен только PAID."""
    svc = _service(
        [_lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A")],
        [_teacher()],
        [_payment("STU-A", "TCH-0001", "2026-05", PaymentStatus.PENDING)],
    )
    assert _run(svc.compute_debt_map()) == {"STU-A": {"2026-05": 1000}}


def test_group_abonement_amount_zero_not_counted():
    """В PER_VISIT-группе платит только тот, у кого amount>0; amount=0 = абонемент."""
    svc = _service(
        [_lesson("LES-1", "TCH-0001", "2026-05-10",
                 lesson_type=LessonType.GROUP, duration=60,
                 attendees="STU-A:60:700,STU-B:60:0")],
        [_teacher()], [],
    )
    assert _run(svc.compute_debt_map()) == {"STU-A": {"2026-05": 700}}


def test_multi_period_aggregation():
    svc = _service(
        [
            _lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A"),
            _lesson("LES-2", "TCH-0001", "2026-06-11", s1="STU-A"),
            _lesson("LES-3", "TCH-0001", "2026-06-18", s1="STU-A"),
        ],
        [_teacher()], [],
    )
    assert _run(svc.compute_debt_map()) == {
        "STU-A": {"2026-05": 1000, "2026-06": 2000},
    }


def test_paid_one_teacher_keeps_debt_to_another():
    svc = _service(
        [
            _lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A"),
            _lesson("LES-2", "TCH-0002", "2026-05-12", s1="STU-A"),
        ],
        [_teacher("TCH-0001"), _teacher("TCH-0002", rate_for_student=1200)],
        [_payment("STU-A", "TCH-0001", "2026-05", PaymentStatus.PAID)],
    )
    assert _run(svc.compute_debt_map()) == {"STU-A": {"2026-05": 1200}}


def test_lesson_with_unknown_teacher_skipped():
    svc = _service(
        [_lesson("LES-1", "TCH-GONE", "2026-05-10", s1="STU-A")],
        [_teacher("TCH-0001")], [],
    )
    assert _run(svc.compute_debt_map()) == {}
