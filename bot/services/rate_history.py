"""История ставок педагогов: старые цены для прошлых занятий.

Лист `teacher_rate_history`: teacher_id | until_period | rate_group |
rate_for_teacher | rate_for_student — «эти ставки действуют по until_period
включительно». Граница пишется месяцем (`YYYY-MM` — по конец месяца) или
днём (`YYYY-MM-DD` — цена выросла в середине месяца, напр. с 4 сентября).
Берётся строка с наименьшей границей ≥ даты занятия; если такой нет —
текущие ставки из карточки педагога.

Кэш в памяти: загружается при старте и обновляется фоном (см. __main__),
чтобы чистые функции billing_service оставались синхронными.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateRow:
    teacher_id: str
    until_period: str  # YYYY-MM (по конец месяца) или YYYY-MM-DD (по этот день)
    rate_group: int
    rate_for_teacher: int
    rate_for_student: int


_rows: list[RateRow] = []


def load(rows: list[RateRow]) -> None:
    global _rows
    _rows = list(rows)


def _as_day(value: str, month_end: bool) -> str:
    """Месяц → крайний день месяца («2026-09» → «2026-09-31»); день остаётся днём."""
    return value if len(value) == 10 else f"{value}-31" if month_end else f"{value}-01"


def effective_rates(teacher, when: str | None) -> tuple[int, int, int]:
    """(rate_group, rate_for_teacher, rate_for_student) педагога на дату занятия.

    `when` — дата `YYYY-MM-DD` (точнее: учитывает повышение в середине месяца)
    или месяц `YYYY-MM` (тогда берутся ставки, действующие к концу месяца).
    """
    if when:
        day = _as_day(when, month_end=True)
        matching = [
            r for r in _rows
            if r.teacher_id == teacher.teacher_id and _as_day(r.until_period, month_end=True) >= day
        ]
        if matching:
            r = min(matching, key=lambda x: _as_day(x.until_period, month_end=True))
            return r.rate_group, r.rate_for_teacher, r.rate_for_student
    return teacher.rate_group, teacher.rate_for_teacher, teacher.rate_for_student
