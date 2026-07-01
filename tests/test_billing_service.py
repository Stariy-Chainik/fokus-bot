"""Характеризующие тесты для расчёта сумм (bot/services/billing_service.py).

Фиксируют ТЕКУЩЕЕ поведение формул зарплаты и счёта — эталон для рефакторинга.
"""
from bot.models import Teacher, Lesson
from bot.models.enums import LessonType
from bot.services.billing_service import calc_earned, build_billing_rows


def _teacher(rate_group=500, rate_for_teacher=800, rate_for_student=900) -> Teacher:
    return Teacher(
        teacher_id="TCH-0001", tg_id=None, name="Тест",
        rate_group=rate_group, rate_for_teacher=rate_for_teacher,
        rate_for_student=rate_for_student,
    )


def _lesson(**over) -> Lesson:
    base = dict(
        lesson_id="LES-000001", teacher_id="TCH-0001", teacher_name="Тест",
        type=LessonType.INDIVIDUAL,
        student_1_id=None, student_1_name=None,
        student_2_id=None, student_2_name=None,
        date="2026-04-23", duration_min=45, earned=0,
        recorded_at="2026-04-23 10:00:00", updated_at="2026-04-23 10:00:00",
        attendees=None, group_id="",
    )
    base.update(over)
    return Lesson(**base)


# ── calc_earned ────────────────────────────────────────────────────────────

def test_calc_earned_group_uses_rate_group():
    t = _teacher(rate_group=500)
    assert calc_earned(LessonType.GROUP, 45, t) == 500
    assert calc_earned(LessonType.GROUP, 60, t) == 667   # round(666.67)
    assert calc_earned(LessonType.GROUP, 90, t) == 1000


def test_calc_earned_individual_uses_rate_for_teacher():
    t = _teacher(rate_for_teacher=800)
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 800
    assert calc_earned(LessonType.INDIVIDUAL, 90, t) == 1600


def test_calc_earned_independent_of_student_count():
    # earned не зависит от числа учеников — только ставка и длительность.
    t = _teacher(rate_for_teacher=800)
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 800


# ── build_billing_rows: групповое ──────────────────────────────────────────

def test_group_without_attendees_returns_empty():
    rows = build_billing_rows(_lesson(type=LessonType.GROUP, attendees=None), _teacher())
    assert rows == []


def test_group_bills_only_positive_amounts():
    lesson = _lesson(type=LessonType.GROUP, duration_min=60,
                     attendees="STU-1:60:700,STU-2:60:0,STU-3:60:700")
    rows = build_billing_rows(lesson, _teacher())
    assert [(r.student_id, r.amount, r.duration_min) for r in rows] == [
        ("STU-1", 700, 60),
        ("STU-3", 700, 60),
    ]


def test_group_old_format_amounts_zero_not_billed():
    # Старый формат attendees (без сумм) = абонемент → строк нет.
    lesson = _lesson(type=LessonType.GROUP, attendees="STU-1,STU-2")
    assert build_billing_rows(lesson, _teacher()) == []


# ── build_billing_rows: индивидуальное / пара ──────────────────────────────

def test_individual_single_student_gets_full_amount():
    t = _teacher(rate_for_student=900)
    lesson = _lesson(duration_min=45, student_1_id="STU-1", student_1_name="Иванов")
    rows = build_billing_rows(lesson, t)
    assert len(rows) == 1
    assert rows[0].student_id == "STU-1"
    assert rows[0].amount == 900


def test_pair_splits_equally_remainder_to_first():
    # base = round(901*45/45) = 901; per=450, остаток 1 → первому 451.
    t = _teacher(rate_for_student=901)
    lesson = _lesson(
        duration_min=45,
        student_1_id="STU-1", student_1_name="Первый",
        student_2_id="STU-2", student_2_name="Второй",
    )
    rows = build_billing_rows(lesson, t)
    amounts = [(r.student_id, r.amount) for r in rows]
    assert amounts == [("STU-1", 451), ("STU-2", 450)]
    # Сумма долей точно равна полной стоимости.
    assert sum(r.amount for r in rows) == 901


def test_three_students_split_remainder_to_first():
    # base = round(1000*45/45) = 1000; per=333, остаток 1 → первому 334.
    t = _teacher(rate_for_student=1000)
    lesson = _lesson(
        duration_min=45,
        student_1_id="STU-1", student_1_name="A",
        student_2_id="STU-2", student_2_name="B",
        student_3_id="STU-3", student_3_name="C",
    )
    rows = build_billing_rows(lesson, t)
    assert [(r.student_id, r.amount) for r in rows] == [
        ("STU-1", 334), ("STU-2", 333), ("STU-3", 333),
    ]
    assert sum(r.amount for r in rows) == 1000


def test_individual_no_participants_returns_empty():
    assert build_billing_rows(_lesson(), _teacher()) == []


def test_billing_row_period_month_derived_from_date():
    t = _teacher(rate_for_student=900)
    lesson = _lesson(date="2026-04-23", student_1_id="STU-1", student_1_name="X")
    rows = build_billing_rows(lesson, t)
    assert rows[0].period_month == "2026-04"
