"""Текст счёта родителю (utils/bill_format.build_bill_text) — снимок текущего формата."""
from types import SimpleNamespace

from bot.services.payment_ledger import BillAggregate
from bot.utils.bill_format import build_bill_text
from tests.fakes import assert_golden


def _item(date, duration, amount):
    return SimpleNamespace(date=date, duration_min=duration, amount=amount)


def _bills():
    return {
        # порядок дат намеренно перепутан — счёт сортирует сам
        "TCH-0001": BillAggregate("Река Станислав", 4600,
                                  items=[_item("2026-09-05", 60, 2500), _item("2026-09-02", 45, 2100), _item("2026-09-05", 45, 0)]),
        "SUB:GRP-0001": BillAggregate("Абонемент", 7000, subscription=True),
    }


def test_bill_text_unpaid():
    text, total = build_bill_text("Иванов Иван", ["БП БТ Спортивная", "ЮБ сад БТ"], "2026-09", _bills())
    assert total == 11600
    assert_golden("bill_text_unpaid", text)


def test_bill_text_partially_paid_shows_remainder():
    text, total = build_bill_text("Иванов Иван", [], "2026-09", _bills(), paid=4600)
    assert total == 11600
    assert "Начислено: 11600 ₽ · оплачено: 4600 ₽" in text
    assert "<b>Остаток к оплате: 7000 ₽</b>" in text
    assert "Группы:" not in text
    assert_golden("bill_text_paid_part", text)


def test_bill_text_overpaid_remainder_is_zero():
    text, _ = build_bill_text("Иванов Иван", [], "2026-09", {"T": BillAggregate("Река", 100)}, paid=250)
    assert "<b>Остаток к оплате: 0 ₽</b>" in text
