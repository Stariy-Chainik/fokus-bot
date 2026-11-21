"""Тесты единого форматирования денежных сумм (`bot/utils/format.py`)."""
from __future__ import annotations

from bot.utils.format import format_amount, format_money


def test_format_amount() -> None:
    assert format_amount(0) == "0"
    assert format_amount(None) == "0"
    assert format_amount(500) == "500"
    assert format_amount(1200) == "1 200"
    assert format_amount(1234567) == "1 234 567"
    assert format_amount(-4500) == "-4 500"
    assert format_amount(1250.6) == "1 251"


def test_format_money() -> None:
    assert format_money(0) == "0 ₽"
    assert format_money(None) == "0 ₽"
    assert format_money(800) == "800 ₽"
    assert format_money(1200) == "1 200 ₽"
    assert format_money(12000) == "12 000 ₽"
    assert format_money(-3500) == "-3 500 ₽"
    assert format_money(3500, signed=True) == "+3 500 ₽"
    assert format_money(0, signed=True) == "0 ₽"
    assert format_money(-3500, signed=True) == "-3 500 ₽"
    assert format_money("1200.00") == "1 200 ₽"
    assert format_money("?") == "? ₽"
