"""История ставок педагогов: старые цены для прошлых месяцев.

Лист `teacher_rate_history`: teacher_id | until_period | rate_group |
rate_for_teacher | rate_for_student — «эти ставки действуют по месяц
until_period включительно». Для периода берётся строка с наименьшим
until_period ≥ period; если такой нет — текущие ставки из карточки педагога.

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
    until_period: str  # YYYY-MM
    rate_group: int
    rate_for_teacher: int
    rate_for_student: int


_rows: list[RateRow] = []


def load(rows: list[RateRow]) -> None:
    global _rows
    _rows = list(rows)


def effective_rates(teacher, period: str | None) -> tuple[int, int, int]:
    """(rate_group, rate_for_teacher, rate_for_student) педагога на период."""
    if period:
        matching = [
            r for r in _rows
            if r.teacher_id == teacher.teacher_id and r.until_period >= period
        ]
        if matching:
            r = min(matching, key=lambda x: x.until_period)
            return r.rate_group, r.rate_for_teacher, r.rate_for_student
    return teacher.rate_group, teacher.rate_for_teacher, teacher.rate_for_student
