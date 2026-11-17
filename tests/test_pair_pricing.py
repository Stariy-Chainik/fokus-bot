"""Согласованные доли пары: цена урока, аренда и прямая оплата не смешиваются со сбором школы."""
from dataclasses import replace

import pytest

from bot.models.enums import LessonType
from bot.services.billing_service import build_billing_rows, round_month
from bot.services.payment_ledger import direct_pay_rows
from bot.services.profit_service import calculate_profit_lesson, lesson_rent
from config.settings import settings
from tests.test_billing_service import _lesson, _teacher


@pytest.fixture
def agreement(monkeypatch):
    monkeypatch.setattr(settings, "pair_lesson_prices", "TCH-0001:STU-1:1000:STU-2:1600:2026-11-09:800")
    return _lesson(date="2026-11-14", duration_min=90, student_1_id="STU-1", student_1_name="А",
                   student_2_id="STU-2", student_2_name="Б")


@pytest.mark.parametrize("minutes", [45, 60, 90])
def test_agreed_shares_are_for_whole_lesson_and_follow_student_not_slot(agreement, minutes, monkeypatch):
    monkeypatch.setattr(settings, "student_discounts", "STU-1:10")
    ls = replace(agreement, duration_min=minutes, student_1_id="STU-2", student_2_id="STU-1")
    rows = round_month(build_billing_rows(ls, _teacher()))
    assert {r.student_id: r.amount for r in rows} == {"STU-1": 1000, "STU-2": 1600}


@pytest.mark.parametrize("changes", [
    {"date": "2026-11-08"}, {"student_2_id": "STU-3"}, {"student_2_id": None},
    {"student_3_id": "STU-3"}, {"teacher_id": "TCH-other"},
])
def test_other_lessons_keep_standard_pricing(agreement, changes):
    ls = replace(agreement, **changes)
    assert sum(r.amount for r in build_billing_rows(ls, _teacher())) == 1800


def test_direct_lesson_has_one_flat_rent_and_teacher_keeps_remainder(agreement, monkeypatch):
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "hall_rent_since_period", "2026-11")
    monkeypatch.setattr(settings, "hall_rent_per_lesson", "TCH-0001:500")
    teacher = _teacher()
    assert build_billing_rows(agreement, teacher) == []
    assert lesson_rent(agreement) == 800          # за 90 минут один раз, не 1600
    gross = sum(r.amount for r in build_billing_rows(agreement, teacher, include_direct=True))
    assert gross == 2600 and gross - lesson_rent(agreement) == 1800
    profit = calculate_profit_lesson(agreement, teacher)
    assert profit is not None and (profit.income, profit.salary, profit.rent) == (800, 0, 800)
    assert lesson_rent(replace(agreement, type=LessonType.GROUP)) == 0


def test_direct_costs_have_no_parent_payment_status(agreement, monkeypatch):
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    lessons = [agreement, replace(agreement, lesson_id="LES-000002")]
    teachers = {"TCH-0001": _teacher()}
    polina = [x for row in direct_pay_rows("STU-2", lessons, teachers) for x in row.lessons]
    anton = [x for row in direct_pay_rows("STU-1", lessons, teachers) for x in row.lessons]
    assert [x.amount for x in polina] == [1600, 1600]
    assert [x.amount for x in anton] == [1000, 1000]
    assert all(not hasattr(x, "paid") for x in polina + anton)


def test_latest_valid_agreement_and_reka_distribution(agreement, monkeypatch):
    monkeypatch.setattr(settings, "pair_lesson_prices", ",".join([
        "TCH-0001:STU-1:1250:STU-2:1750:2026-11-09",
        "TCH-0001:STU-1:1:STU-2:2:2026-12-01",
        "TCH-0001:STU-1:1:STU-2:2:bad-date",
        "TCH-0001:STU-1:1:STU-2:2:2026-11-10:999",
    ]))
    assert [r.amount for r in build_billing_rows(agreement, _teacher())] == [1250, 1750]
