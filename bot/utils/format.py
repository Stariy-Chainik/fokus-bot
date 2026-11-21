"""Единое форматирование денежных сумм в текстах бота и уведомлениях."""
from __future__ import annotations


def _to_int(amount: int | float | str | None) -> int | None:
    if amount is None or amount == "":
        return 0
    if isinstance(amount, (int, float)):
        return round(amount)
    try:
        return round(float(str(amount).replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def format_amount(amount: int | float | str | None) -> str:
    """Форматирует число с пробелом-разделителем тысяч: 1200 → '1 200', -500 → '-500'."""
    value = _to_int(amount)
    if value is None:
        return str(amount)
    return f"{value:,}".replace(",", " ")


def format_money(amount: int | float | str | None, *, signed: bool = False) -> str:
    """Форматирует сумму в едином формате: 1200 → '1 200 ₽', -500 → '-500 ₽'."""
    value = _to_int(amount)
    if value is None:
        return f"{amount} ₽"
    sign = "+" if signed and value > 0 else ("-" if value < 0 else "")
    body = f"{abs(value):,}".replace(",", " ")
    return f"{sign}{body} ₽"
