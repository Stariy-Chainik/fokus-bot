"""Экран «Счёт ученика за период»: бинарные статусы уроков."""
from types import SimpleNamespace

from bot.handlers.admin.bills.helpers import _bill_detail_lines
from bot.models.enums import PaymentStatus


def _lesson(lesson_id: str, date: str, amount: int, lesson_type: str = "individual"):
    return SimpleNamespace(
        lesson_id=lesson_id, date=date, duration_min=60, amount=amount,
        lesson_type=lesson_type,
    )


def _payment(amount: int, status: PaymentStatus, paid_at: str | None = None):
    return SimpleNamespace(
        teacher_id="T1", total_amount=amount, status=status, paid_at=paid_at,
    )


def test_unpaid_lessons_are_listed_before_paid_lessons():
    bills = {
        "T1": {
            "name": "Мария Иванова",
            "total": 3900,
            "items": [
                _lesson("LES-1", "2026-09-05", 1300),
                _lesson("LES-2", "2026-09-12", 1300, "group"),
                _lesson("LES-3", "2026-09-19", 1300),
            ],
        },
    }
    payments = [
        _payment(2600, PaymentStatus.PAID, "2026-09-12 12:00:00"),
        _payment(1300, PaymentStatus.PENDING),
    ]

    text = "\n".join(_bill_detail_lines("Алиса", "2026-09", bills, payments))

    assert "<b>⬜ К оплате:</b>" in text
    assert "⬜ 19 сен, сб · индивидуальное · 60 мин · 1300 руб." in text
    assert "<b>✅ Оплачено:</b>" in text
    assert "✅ 12 сен, сб · групповое · 60 мин · 1300 руб." in text
    assert text.index("⬜ 19 сен") < text.index("✅ 5 сен")
    assert "🟡" not in text
    assert "частич" not in text.lower()
    assert "к оплате 1300" in text


def test_subscription_has_one_binary_status():
    bills = {
        "SUB:G1": {
            "name": "Абонемент «Юниоры»",
            "total": 7000,
            "items": [],
            "subscription": True,
        },
    }
    payments = [SimpleNamespace(
        teacher_id="SUB:G1", total_amount=7000, status=PaymentStatus.PENDING, paid_at=None,
    )]

    text = "\n".join(_bill_detail_lines("Алиса", "2026-09", bills, payments))

    assert "⬜ Фиксированная сумма за месяц" in text
    assert "🟡" not in text
    assert "частич" not in text.lower()
