"""Тесты абонементных начислений (GroupBillingMode.SUBSCRIPTION).

Правило: группа-абонемент → фиксированная price_full ₽/месяц с каждого ученика
группы, независимо от числа занятий; начисляется только за месяц, в котором у
группы было хотя бы одно занятие. Ключ начисления — SUB:{group_id}.
"""
import asyncio

from bot.models import Teacher, Lesson, Group, StudentPeriodPayment
from bot.models.enums import LessonType, PaymentStatus, GroupBillingMode
from bot.services.payment_service import PaymentService


def _run(coro):
    return asyncio.run(coro)


class _FakeLessonRepo:
    def __init__(self, lessons):
        self._lessons = lessons

    async def get_all(self):
        return self._lessons

    async def get_by_student_and_period(self, student_id, period):
        out = []
        for ls in self._lessons:
            if ls.date[:7] != period:
                continue
            ids = {ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id}
            if student_id in ids:
                out.append(ls)
        return out


class _FakeTeacherRepo:
    def __init__(self, teachers):
        self._teachers = teachers

    async def get_all(self):
        return self._teachers

    async def get_by_id(self, tid):
        return next((t for t in self._teachers if t.teacher_id == tid), None)


class _FakePaymentRepo:
    def __init__(self, payments):
        self._payments = payments

    async def get_all(self):
        return self._payments


class _FakeGroupRepo:
    def __init__(self, groups):
        self._groups = groups

    async def get_all(self):
        return self._groups

    async def get_by_id(self, gid):
        return next((g for g in self._groups if g.group_id == gid), None)


class _FakeStudentGroupRepo:
    def __init__(self, pairs):
        self._pairs = pairs  # list[(student_id, group_id)]

    async def get_groups_for_student(self, sid):
        return [g for s, g in self._pairs if s == sid]

    async def get_students_for_group(self, gid):
        return [s for s, g in self._pairs if g == gid]


def _teacher(tid="TCH-0001"):
    return Teacher(teacher_id=tid, tg_id=None, name="Педагог",
                   rate_group=800, rate_for_teacher=900, rate_for_student=1000)


def _sub_group(gid="GRP-0001", price=3000):
    return Group(group_id=gid, branch_id="BRN-0001", name="Хип-хоп дети",
                 billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=price)


def _group_lesson(lesson_id, gid, lesson_date, teacher_id="TCH-0001"):
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher_id, teacher_name="—",
        type=LessonType.GROUP, student_1_id=None, student_1_name=None,
        student_2_id=None, student_2_name=None,
        date=lesson_date, duration_min=60, earned=0,
        recorded_at="", updated_at="", attendees=None, group_id=gid,
    )


def _solo_lesson(lesson_id, sid, lesson_date, teacher_id="TCH-0001"):
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher_id, teacher_name="—",
        type=LessonType.INDIVIDUAL, student_1_id=sid, student_1_name=sid,
        student_2_id=None, student_2_name=None,
        date=lesson_date, duration_min=45, earned=0,
        recorded_at="", updated_at="",
    )


def _payment(sid, tid, period, status):
    return StudentPeriodPayment(
        payment_id="PAY-000001", student_id=sid, student_name="—",
        period_month=period, total_amount=0, status=status,
        paid_at=None, confirmed_by_tg_id=None, comment=None,
        created_at="", updated_at="", teacher_id=tid, teacher_name="—",
    )


def _service(lessons, groups, pairs, payments=()):
    return PaymentService(
        payment_repo=_FakePaymentRepo(list(payments)),
        lesson_repo=_FakeLessonRepo(lessons),
        teacher_repo=_FakeTeacherRepo([_teacher()]),
        group_repo=_FakeGroupRepo(groups),
        student_group_repo=_FakeStudentGroupRepo(pairs),
    )


def test_subscription_billed_once_regardless_of_lesson_count():
    """2 занятия в месяце → всё равно одна фикс-сумма."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03"),
         _group_lesson("LES-2", "GRP-0001", "2026-07-10")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
    )
    bills = _run(svc.compute_bills_for_student_period("STU-A", "2026-07"))
    assert list(bills) == ["SUB:GRP-0001"]
    agg = bills["SUB:GRP-0001"]
    assert agg["total"] == 3000
    assert agg["subscription"] is True
    assert agg["name"] == "Абонемент «Хип-хоп дети»"


def test_no_lessons_in_month_no_subscription():
    """Каникулы: занятий в месяце нет → абонемент не начисляется."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-06-15")],  # только июнь
        [_sub_group()],
        [("STU-A", "GRP-0001")],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}


def test_subscription_merges_with_individual_lessons():
    """Абонемент + индивидуальное занятие — оба в счёте."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03"),
         _solo_lesson("LES-2", "STU-A", "2026-07-05")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
    )
    bills = _run(svc.compute_bills_for_student_period("STU-A", "2026-07"))
    assert bills["SUB:GRP-0001"]["total"] == 3000
    assert bills["TCH-0001"]["total"] == 1000  # 1000 × 45/45


def test_debt_map_accrues_subscription_per_member():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
    )
    debts = _run(svc.compute_debt_map())
    assert debts == {"STU-A": {"2026-07": 3000}, "STU-B": {"2026-07": 3000}}


def test_debt_map_paid_subscription_excluded():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
        payments=[_payment("STU-A", "SUB:GRP-0001", "2026-07", PaymentStatus.PAID)],
    )
    assert _run(svc.compute_debt_map()) == {}


def test_zero_price_subscription_not_billed():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=0)],
        [("STU-A", "GRP-0001")],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_debt_map()) == {}


def test_none_and_per_visit_groups_not_affected():
    """NONE-группа не начисляет абонемент; PER_VISIT работает по посещениям как раньше."""
    none_group = Group(group_id="GRP-0002", branch_id="BRN-0001", name="Бесплатная",
                       billing_mode=GroupBillingMode.NONE)
    svc = _service(
        [_group_lesson("LES-1", "GRP-0002", "2026-07-03")],
        [none_group],
        [("STU-A", "GRP-0002")],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_debt_map()) == {}


def test_without_group_repos_subscriptions_skipped():
    """Обратная совместимость: без group/student_group репозиториев подписки не считаются."""
    svc = PaymentService(
        payment_repo=_FakePaymentRepo([]),
        lesson_repo=_FakeLessonRepo([_group_lesson("LES-1", "GRP-0001", "2026-07-03")]),
        teacher_repo=_FakeTeacherRepo([_teacher()]),
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_debt_map()) == {}
