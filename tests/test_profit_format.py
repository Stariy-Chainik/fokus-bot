"""Математика экрана «Прибыль»: занятия + абонементы + ручные доходы − расходы."""
from bot.handlers.admin.profit import _format_profit
from bot.models import FinanceEntry
from bot.services import ProfitSummary, SubscriptionProfitRow, TeacherProfitRow


def _fin(kind, title, amount):
    return FinanceEntry(entry_id="FIN-000001", period_month="2026-07",
                        kind=kind, title=title, amount=amount)


def _teacher_row():
    return TeacherProfitRow(
        teacher_id="TCH-1",
        teacher_name="Иванова",
        income=10000,
        salary=4000,
        group_lessons=2,
        individual_lessons=3,
    )


def test_profit_includes_manual_income_and_expenses():
    summary = ProfitSummary(
        period="2026-07",
        teacher_rows=(_teacher_row(),),
        subscription_rows=(SubscriptionProfitRow("Азбука", 3, 6000),),
        finance_entries=(
            _fin("income", "Турнир «Кубок»", 15000),
            _fin("expense", "Аренда зала", 20000),
        ),
    )
    text = _format_profit(
        "Прибыль за 07.2026",
        summary,
    )
    # Выручка = 10000 + 6000 + 15000 = 31000; расходы = 4000 + 20000
    assert "Выручка:    31000 ₽" in text
    assert "Расходы:   20000 ₽" in text
    assert "Прибыль:  7000 ₽" in text          # 31000 − 24000
    assert "Турнир «Кубок»: 15000 ₽" in text
    assert "Аренда зала: 20000 ₽" in text
    assert "Азбука: 6000 ₽ (3 уч.)" in text


def test_profit_without_manual_entries_unchanged():
    summary = ProfitSummary(period="2026-07", teacher_rows=(_teacher_row(),))
    text = _format_profit("Прибыль за 07.2026", summary)
    assert "Выручка:    10000 ₽" in text
    assert "Прибыль:  6000 ₽" in text
    assert "Расходы" not in text


def test_profit_shows_hall_rent_share():
    summary = ProfitSummary(
        period="2026-09",
        teacher_rows=(TeacherProfitRow(
            teacher_id="TCH-2", teacher_name="Клецова",
            income=6000, salary=0,
            group_lessons=0, individual_lessons=12, rent=6000, rent_lessons=12,
        ),),
    )
    text = _format_profit("Прибыль за 09.2026", summary)
    assert "🏟 в т.ч. аренда зала: 6000 ₽ (12 зан.)" in text
    assert "Прибыль:  6000 ₽" in text


def test_profit_shows_owner_salary_kept_in_profit():
    summary = ProfitSummary(
        period="2026-09",
        teacher_rows=(
            TeacherProfitRow(
                teacher_id="TCH-1", teacher_name="Река",
                income=50000, salary=0, group_lessons=1, individual_lessons=9,
                owner=True, owner_income=50000,
            ),
            _teacher_row(),
        ),
    )
    text = _format_profit("Прибыль за 09.2026", summary)
    assert "  Выручка: 50000 ₽  Зарплата: 0 ₽" in text
    assert "  👑 руководитель: 50000 ₽ остаются в прибыли" in text
    assert "Зарплата: 4000 ₽\n  👑 руководитель в прибыли: 50000 ₽" in text
    assert "Прибыль:  56000 ₽" in text                # 60000 − 4000
