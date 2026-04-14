from __future__ import annotations
import calendar
from datetime import date

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
_WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


def kb_calendar(
    year: int, month: int, prefix: str,
    min_date: date | None = None, max_date: date | None = None,
    cancel_cb: str = "teacher:menu",
) -> InlineKeyboardMarkup:
    """Inline-календарь на указанный месяц.

    Callbacks:
      - {prefix}_nav:YYYY-MM   — навигация между месяцами
      - {prefix}_pick:YYYY-MM-DD — выбор дня
      - noop — дни вне диапазона и заголовки
    """
    rows: list[list[InlineKeyboardButton]] = []

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, +1)
    header = f"{_MONTHS_RU[month - 1]} {year}"
    rows.append([
        InlineKeyboardButton(text="‹", callback_data=f"{prefix}_nav:{prev_y}-{prev_m:02d}"),
        InlineKeyboardButton(text=header, callback_data="noop"),
        InlineKeyboardButton(text="›", callback_data=f"{prefix}_nav:{next_y}-{next_m:02d}"),
    ])
    rows.append([InlineKeyboardButton(text=wd, callback_data="noop") for wd in _WEEKDAYS_RU])

    for week in calendar.monthcalendar(year, month):
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
                continue
            d = date(year, month, day)
            if (min_date and d < min_date) or (max_date and d > max_date):
                row.append(InlineKeyboardButton(text=f"·{day}·", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day), callback_data=f"{prefix}_pick:{d.isoformat()}",
                ))
        rows.append(row)

    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
