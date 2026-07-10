"""Доменный расчёт прибыли — эталон для Telegram и будущего Web API."""
from __future__ import annotations

from bot.models import FinanceEntry, Lesson, Teacher
from bot.models.enums import LessonType
from bot.services import (
    ProfitSummary,
    SubscriptionProfitRow,
    build_teacher_profit_detail,
    calculate_teacher_profit,
)


def _teacher() -> Teacher:
    return Teacher(
        teacher_id="TCH-0001",
        tg_id=None,
        name="Тест",
        rate_group=500,
        rate_for_teacher=800,
        rate_for_student=900,
    )


def _lesson(lesson_id: str, lesson_date: str, **overrides) -> Lesson:
    values = {
        "lesson_id": lesson_id,
        "teacher_id": "TCH-0001",
        "teacher_name": "Тест",
        "type": LessonType.INDIVIDUAL,
        "student_1_id": "STU-0001",
        "student_1_name": "Ученик",
        "student_2_id": None,
        "student_2_name": None,
        "date": lesson_date,
        "duration_min": 45,
        "earned": 0,
        "recorded_at": f"{lesson_date} 10:00:00",
        "updated_at": f"{lesson_date} 10:00:00",
        "attendees": None,
        "group_id": "",
    }
    values.update(overrides)
    return Lesson(**values)


def test_teacher_profit_uses_only_billable_lessons():
    lessons = [
        _lesson("LES-000001", "2026-07-01"),
        _lesson(
            "LES-000002",
            "2026-07-02",
            type=LessonType.GROUP,
            student_1_id=None,
            student_1_name=None,
            attendees=None,
            group_id="GRP-0001",
        ),
    ]

    row = calculate_teacher_profit(_teacher(), lessons)

    assert row is not None
    assert row.income == 900
    assert row.salary == 800
    assert row.group_lessons == 0
    assert row.individual_lessons == 1


def test_group_per_visit_contributes_income_and_group_salary():
    lesson = _lesson(
        "LES-000001",
        "2026-07-01",
        type=LessonType.GROUP,
        student_1_id=None,
        student_1_name=None,
        duration_min=60,
        attendees="STU-0001:60:700,STU-0002:60:700",
        group_id="GRP-0001",
    )

    row = calculate_teacher_profit(_teacher(), [lesson])

    assert row is not None
    assert row.income == 1400
    assert row.salary == 667
    assert row.group_lessons == 1
    assert row.individual_lessons == 0


def test_teacher_detail_is_sorted_and_has_derived_totals():
    detail = build_teacher_profit_detail(
        _teacher(),
        [
            _lesson("LES-000002", "2026-07-20", duration_min=90),
            _lesson("LES-000001", "2026-07-01"),
        ],
        "2026-07",
    )

    assert [row.lesson_id for row in detail.lessons] == [
        "LES-000001",
        "LES-000002",
    ]
    assert detail.income == 2700
    assert detail.salary == 2400
    assert detail.profit == 300
    assert detail.margin_percent == 11


def test_profit_summary_combines_all_income_and_expenses():
    teacher_row = calculate_teacher_profit(
        _teacher(),
        [_lesson("LES-000001", "2026-07-01")],
    )
    assert teacher_row is not None
    summary = ProfitSummary(
        period="2026-07",
        teacher_rows=(teacher_row,),
        subscription_rows=(SubscriptionProfitRow("Группа", 2, 4000),),
        finance_entries=(
            FinanceEntry("FIN-000001", "2026-07", "income", "Турнир", 5000),
            FinanceEntry("FIN-000002", "2026-07", "expense", "Аренда", 3000),
        ),
    )

    assert summary.total_income == 9900
    assert summary.total_expenses == 3800
    assert summary.profit == 6100
