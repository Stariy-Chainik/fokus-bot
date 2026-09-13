"""Тесты абонементных начислений (GroupBillingMode.SUBSCRIPTION).

Правило: группа-абонемент → фиксированная price_full ₽/месяц с каждого ученика
группы, независимо от числа занятий; начисляется только за месяц, в котором у
группы было хотя бы одно занятие. Ключ начисления — SUB:{group_id}.
"""
import asyncio

from bot.models import StudentGroup, Teacher, Lesson, Group, StudentPeriodPayment, SubscriptionOverride
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

    async def get_all(self, include_archived: bool = False):
        return [g for g in self._groups if include_archived or not g.archived]

    async def get_by_id(self, gid):
        return next((g for g in self._groups if g.group_id == gid), None)


class _FakeStudentGroupRepo:
    def __init__(self, pairs, joined=None, left=None):
        self._pairs = [(p[0], p[1]) for p in pairs]  # list[(student_id, group_id)]
        # joined/left: {(sid, gid): "YYYY-MM"} — месяцы вступления и ухода
        self._joined = dict(joined or {})
        self._left = dict(left or {})

    def _row(self, sid, gid):
        return StudentGroup(student_id=sid, group_id=gid,
                            joined_period=self._joined.get((sid, gid), ""),
                            left_period=self._left.get((sid, gid), ""))

    async def get_groups_for_student(self, sid, include_left=False):
        return [g for s, g in self._pairs
                if s == sid and (include_left or self._row(s, g).is_active)]

    async def get_students_for_group(self, gid, include_left=False):
        return [s for s, g in self._pairs
                if g == gid and (include_left or self._row(s, g).is_active)]

    async def get_membership_map(self):
        return {(s, g): self._row(s, g) for s, g in self._pairs}


def _teacher(tid="TCH-0001"):
    return Teacher(teacher_id=tid, tg_id=None, name="Педагог",
                   rate_group=800, rate_for_teacher=900, rate_for_student=1000)


def _sub_group(gid="GRP-0001", price=3000):
    return Group(group_id=gid, branch_id="BRN-0001", name="Хип-хоп дети",
                 billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=price)


def _per_visit_group(gid="GRP-0002", price=700):
    return Group(group_id=gid, branch_id="BRN-0001", name="Поштучная",
                 billing_mode=GroupBillingMode.PER_VISIT, price_full=price)


def _group_lesson(lesson_id, gid, lesson_date, teacher_id="TCH-0001", attendees=None):
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher_id, teacher_name="—",
        type=LessonType.GROUP, student_1_id=None, student_1_name=None,
        student_2_id=None, student_2_name=None,
        date=lesson_date, duration_min=60, earned=0,
        recorded_at="", updated_at="", attendees=attendees, group_id=gid,
    )


def _solo_lesson(lesson_id, sid, lesson_date, teacher_id="TCH-0001"):
    return Lesson(
        lesson_id=lesson_id, teacher_id=teacher_id, teacher_name="—",
        type=LessonType.INDIVIDUAL, student_1_id=sid, student_1_name=sid,
        student_2_id=None, student_2_name=None,
        date=lesson_date, duration_min=45, earned=0,
        recorded_at="", updated_at="",
    )


def _payment(sid, tid, period, status, amount=0):
    return StudentPeriodPayment(
        payment_id="PAY-000001", student_id=sid, student_name="—",
        period_month=period, total_amount=amount, status=status,
        paid_at=None, confirmed_by_tg_id=None, comment=None,
        created_at="", updated_at="", teacher_id=tid, teacher_name="—",
    )


class _FakeOverrideRepo:
    def __init__(self, overrides):
        self._overrides = list(overrides)

    async def get_all(self):
        return self._overrides

    async def get_for_group(self, group_id):
        return [o for o in self._overrides if o.group_id == group_id]

    async def upsert(self, group_id, period_month, student_id, amount):
        sid = student_id or None
        for i, o in enumerate(self._overrides):
            if (o.group_id, o.period_month, o.student_id) == (group_id, period_month, sid):
                self._overrides[i] = _override(group_id, period_month, sid, amount)
                return self._overrides[i]
        o = _override(group_id, period_month, sid, amount)
        self._overrides.append(o)
        return o


def _override(gid, period, sid, amount):
    return SubscriptionOverride(group_id=gid, period_month=period,
                                student_id=sid, amount=amount)


def _service(lessons, groups, pairs, payments=(), overrides=None, joined=None, left=None):
    return PaymentService(
        payment_repo=_FakePaymentRepo(list(payments)),
        lesson_repo=_FakeLessonRepo(lessons),
        teacher_repo=_FakeTeacherRepo([_teacher()]),
        group_repo=_FakeGroupRepo(groups),
        student_group_repo=_FakeStudentGroupRepo(pairs, joined, left),
        subscription_override_repo=(
            _FakeOverrideRepo(overrides) if overrides is not None else None
        ),
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
    assert agg["name"] == "Абонемент"


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
    debts = _run(svc.compute_debt_map(until_period="2026-07"))
    assert debts == {"STU-A": {"2026-07": 3000}, "STU-B": {"2026-07": 3000}}


def test_debt_map_paid_subscription_excluded():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
        payments=[_payment("STU-A", "SUB:GRP-0001", "2026-07", PaymentStatus.PAID, amount=3000)],
    )
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {}


def test_zero_price_subscription_not_billed():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=0)],
        [("STU-A", "GRP-0001")],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {}


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
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {}


def test_override_for_whole_group():
    """Цена группы на месяц перекрывает price_full для всех участников."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
        overrides=[_override("GRP-0001", "2026-07", None, 2000)],
    )
    bills = _run(svc.compute_bills_for_student_period("STU-A", "2026-07"))
    assert bills["SUB:GRP-0001"]["total"] == 2000
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {
        "STU-A": {"2026-07": 2000}, "STU-B": {"2026-07": 2000},
    }


def test_override_for_single_student_beats_group_override():
    """Приоритет: цена ученика → цена группы → price_full."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
        overrides=[
            _override("GRP-0001", "2026-07", None, 2000),
            _override("GRP-0001", "2026-07", "STU-A", 1500),
        ],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07"))["SUB:GRP-0001"]["total"] == 1500
    assert _run(svc.compute_bills_for_student_period("STU-B", "2026-07"))["SUB:GRP-0001"]["total"] == 2000


def test_override_zero_exempts_student():
    """0 = освобождение: ученику не начисляется, остальным — как обычно."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
        overrides=[_override("GRP-0001", "2026-07", "STU-A", 0)],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_bills_for_student_period("STU-B", "2026-07"))["SUB:GRP-0001"]["total"] == 3000
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {"STU-B": {"2026-07": 3000}}


def test_override_applies_only_to_its_month():
    """Переопределение на июль не влияет на июнь."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-06-15"),
         _group_lesson("LES-2", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
        overrides=[_override("GRP-0001", "2026-07", None, 2000)],
    )
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {
        "STU-A": {"2026-06": 3000, "2026-07": 2000},
    }


def test_pin_history_fixes_past_active_months_with_old_price():
    """Смена цены «с июля»: июнь/май (были занятия) фиксируются старой ценой."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-05-10"),
         _group_lesson("LES-2", "GRP-0001", "2026-06-15"),
         _group_lesson("LES-3", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
        overrides=[],
    )
    pinned = _run(svc.pin_subscription_history("GRP-0001", "2026-07", 3000))
    assert pinned == 2  # май и июнь; июль (effective) не фиксируется
    # теперь «поднимаем цену»: группа стала 3500 — прошлые месяцы остались по 3000
    svc2 = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-05-10"),
         _group_lesson("LES-2", "GRP-0001", "2026-06-15"),
         _group_lesson("LES-3", "GRP-0001", "2026-07-03")],
        [_sub_group(price=3500)],
        [("STU-A", "GRP-0001")],
        overrides=[_override("GRP-0001", "2026-05", None, 3000),
                   _override("GRP-0001", "2026-06", None, 3000)],
    )
    assert _run(svc2.compute_debt_map(until_period="2026-07")) == {
        "STU-A": {"2026-05": 3000, "2026-06": 3000, "2026-07": 3500},
    }


def test_pin_history_respects_existing_override_and_zero():
    """Существующее переопределение месяца не трогается; pin_amount=0 освобождает прошлое."""
    overrides = [_override("GRP-0001", "2026-05", None, 1000)]  # ручное, сохранить
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-05-10"),
         _group_lesson("LES-2", "GRP-0001", "2026-06-15")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
        overrides=overrides,
    )
    pinned = _run(svc.pin_subscription_history("GRP-0001", "2026-07", 0))
    assert pinned == 1  # только июнь; май уже переопределён вручную
    amounts = {(o.period_month, o.amount) for o in _run(svc._sub_override_repo.get_all())}
    assert ("2026-05", 1000) in amounts and ("2026-06", 0) in amounts


def test_without_group_repos_subscriptions_skipped():
    """Обратная совместимость: без group/student_group репозиториев подписки не считаются."""
    svc = PaymentService(
        payment_repo=_FakePaymentRepo([]),
        lesson_repo=_FakeLessonRepo([_group_lesson("LES-1", "GRP-0001", "2026-07-03")]),
        teacher_repo=_FakeTeacherRepo([_teacher()]),
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-07")) == {}
    assert _run(svc.compute_debt_map(until_period="2026-07")) == {}


def test_subscription_revenue_breakdown_by_group():
    """«Прибыль»: абонементная выручка по группам с учётом переопределений."""
    g2 = Group(group_id="GRP-0002", branch_id="BRN-0001", name="Азбука",
               billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=2000)
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-07-03"),
         _group_lesson("LES-2", "GRP-0002", "2026-07-04")],
        [_sub_group(price=3000), g2],
        [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001"), ("STU-C", "GRP-0002")],
        overrides=[_override("GRP-0001", "2026-07", "STU-B", 0)],  # освобождён
    )
    rows = _run(svc.subscription_revenue_breakdown("2026-07"))
    # сортировка по имени: «Азбука» < «Хип-хоп дети»
    assert rows == [("Азбука", 1, 2000), ("Хип-хоп дети", 1, 3000)]
    # месяц без занятий — пусто
    assert _run(svc.subscription_revenue_breakdown("2026-06")) == []


# ── Правило 2026-09-08: каникулы платные, кроме июля/августа ─────────────────

def test_billable_months_rule():
    from bot.services.payment_service import subscription_billable_months
    # группа начала в мае; занятия в мае, июне, сентябре
    months = subscription_billable_months({"2026-05", "2026-06", "2026-09"}, until="2026-11")
    assert months == {"2026-05", "2026-06", "2026-09", "2026-10", "2026-11"}
    # июль/август без занятий — не платятся; с занятиями — платятся
    assert "2026-08" in subscription_billable_months({"2026-05", "2026-08"}, until="2026-09")
    # до первого занятия группы ничего не начисляется
    assert "2026-04" not in subscription_billable_months({"2026-05"}, until="2026-06")
    assert subscription_billable_months(set(), until="2026-09") == set()


def test_school_holiday_month_is_billed():
    """Занятия только в сентябре; октябрь (каникулы) всё равно начисляется."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-09-05")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
    )
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-10"))["SUB:GRP-0001"]["total"] == 3000
    # а до старта группы — нет
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-06")) == {}


def test_debt_map_includes_holiday_months_but_not_summer():
    svc = _service(
        [_group_lesson("LES-1", "GRP-0001", "2026-05-10")],
        [_sub_group(price=3000)],
        [("STU-A", "GRP-0001")],
    )
    debts = _run(svc.compute_debt_map(until_period="2026-10"))
    assert debts == {"STU-A": {"2026-05": 3000, "2026-06": 3000, "2026-09": 3000, "2026-10": 3000}}


# ── Месяц вступления в группу: абонемент не начисляется задним числом ────────

def _sub_lessons():
    return [_group_lesson("LES-1", "GRP-0001", "2026-04-03"),
            _group_lesson("LES-2", "GRP-0001", "2026-05-06"),
            _group_lesson("LES-3", "GRP-0001", "2026-06-04")]


def test_joined_period_skips_months_before_joining():
    """Ученик вступил в июне — за апрель и май абонемента нет."""
    svc = _service(_sub_lessons(), [_sub_group(price=3000)], [("STU-A", "GRP-0001")],
                   joined={("STU-A", "GRP-0001"): "2026-06"})
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-04")) == {}
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-05")) == {}
    bills = _run(svc.compute_bills_for_student_period("STU-A", "2026-06"))
    assert bills["SUB:GRP-0001"]["total"] == 3000


def test_joined_period_empty_means_from_the_start():
    """Пустой месяц вступления — прежнее поведение: начисляем со всех месяцев группы."""
    svc = _service(_sub_lessons(), [_sub_group(price=3000)], [("STU-A", "GRP-0001")])
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-04"))["SUB:GRP-0001"]["total"] == 3000


def test_debt_map_respects_joined_period():
    svc = _service(_sub_lessons(), [_sub_group(price=3000)],
                   [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
                   joined={("STU-A", "GRP-0001"): "2026-06"})
    debts = _run(svc.compute_debt_map(until_period="2026-06"))
    assert debts["STU-A"] == {"2026-06": 3000}
    assert debts["STU-B"] == {"2026-04": 3000, "2026-05": 3000, "2026-06": 3000}


def test_revenue_breakdown_respects_joined_period():
    svc = _service(_sub_lessons(), [_sub_group(price=3000)],
                   [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
                   joined={("STU-A", "GRP-0001"): "2026-06"})
    assert _run(svc.subscription_revenue_breakdown("2026-04")) == [("Хип-хоп дети", 1, 3000)]
    assert _run(svc.subscription_revenue_breakdown("2026-06")) == [("Хип-хоп дети", 2, 6000)]


def test_joined_period_does_not_touch_per_visit_groups():
    """Месяц вступления касается только абонемента: поштучные занятия считаются как были."""
    svc = _service(
        [_group_lesson("LES-1", "GRP-0002", "2026-04-03", attendees="STU-A:60:700")],
        [_per_visit_group("GRP-0002")],
        [("STU-A", "GRP-0002")],
        joined={("STU-A", "GRP-0002"): "2026-06"},
    )
    assert _run(svc.compute_debt_map(until_period="2026-06")) == {"STU-A": {"2026-04": 700}}


# ── Месяц ухода: прошлые месяцы сохраняются, новые не начисляются ────────────

def test_left_period_keeps_past_months_and_stops_future():
    """Ушёл с июня: апрель и май остаются в долге, июнь уже нет."""
    svc = _service(_sub_lessons(), [_sub_group(price=3000)], [("STU-A", "GRP-0001")],
                   left={("STU-A", "GRP-0001"): "2026-06"})
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-04"))["SUB:GRP-0001"]["total"] == 3000
    assert _run(svc.compute_bills_for_student_period("STU-A", "2026-06")) == {}
    assert _run(svc.compute_debt_map(until_period="2026-06")) == {
        "STU-A": {"2026-04": 3000, "2026-05": 3000}}


def test_left_period_excludes_from_revenue_of_that_month():
    svc = _service(_sub_lessons(), [_sub_group(price=3000)],
                   [("STU-A", "GRP-0001"), ("STU-B", "GRP-0001")],
                   left={("STU-A", "GRP-0001"): "2026-06"})
    assert _run(svc.subscription_revenue_breakdown("2026-05")) == [("Хип-хоп дети", 2, 6000)]
    assert _run(svc.subscription_revenue_breakdown("2026-06")) == [("Хип-хоп дети", 1, 3000)]


def test_joined_and_left_together_bill_only_the_window():
    """Пришёл в мае, ушёл с июня — платит только за май."""
    svc = _service(_sub_lessons(), [_sub_group(price=3000)], [("STU-A", "GRP-0001")],
                   joined={("STU-A", "GRP-0001"): "2026-05"},
                   left={("STU-A", "GRP-0001"): "2026-06"})
    assert _run(svc.compute_debt_map(until_period="2026-06")) == {"STU-A": {"2026-05": 3000}}


def test_membership_covers_helper():
    row = StudentGroup("STU-A", "GRP-0001", joined_period="2026-05", left_period="2026-08")
    assert not row.covers("2026-04") and row.covers("2026-05") and row.covers("2026-07")
    assert not row.covers("2026-08") and not row.is_active
    assert StudentGroup("STU-A", "GRP-0001").covers("2020-01")  # пустые поля — всегда
