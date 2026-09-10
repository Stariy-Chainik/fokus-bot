"""Счета и оплата родителя — транспорт-независимые ряды кнопок (расширяется по мере переноса)."""
from __future__ import annotations

from .types import cb


def bill_back_rows(student_id: str, period_month: str, home_cb: str = "go:home") -> list:
    return [
        [cb("« К счёту", f"client_bill:{student_id}:{period_month}")],
        [cb("« Меню", home_cb)],
    ]
