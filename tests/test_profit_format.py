"""Математика экрана «Прибыль»: занятия + абонементы + ручные доходы − расходы."""
from bot.handlers.admin.profit import _format_profit
from bot.models import FinanceEntry


def _fin(kind, title, amount):
    return FinanceEntry(entry_id="FIN-000001", period_month="2026-07",
                        kind=kind, title=title, amount=amount)


def test_profit_includes_manual_income_and_expenses():
    rows = [("TCH-1", "Иванова", 10000, 4000, 2, 3)]  # выручка 10000, зарплата 4000
    text = _format_profit(
        "Прибыль за 07.2026", rows, total_income=10000, total_salary=4000,
        sub_rows=[("Азбука", 3, 6000)],
        fin_entries=[
            _fin("income", "Турнир «Кубок»", 15000),
            _fin("expense", "Аренда зала", 20000),
        ],
    )
    # Выручка = 10000 + 6000 + 15000 = 31000; расходы = 4000 + 20000
    assert "Выручка:    31000 ₽" in text
    assert "Расходы:   20000 ₽" in text
    assert "Прибыль:  7000 ₽" in text          # 31000 − 24000
    assert "Турнир «Кубок»: 15000 ₽" in text
    assert "Аренда зала: 20000 ₽" in text
    assert "Азбука: 6000 ₽ (3 уч.)" in text


def test_profit_without_manual_entries_unchanged():
    rows = [("TCH-1", "Иванова", 10000, 4000, 2, 3)]
    text = _format_profit("Прибыль за 07.2026", rows, 10000, 4000)
    assert "Выручка:    10000 ₽" in text
    assert "Прибыль:  6000 ₽" in text
    assert "Расходы" not in text
