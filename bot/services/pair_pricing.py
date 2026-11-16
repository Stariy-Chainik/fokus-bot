"""Согласованные доли конкретной пары за целое занятие, независимо от минут.

PAIR_LESSON_PRICES: teacher:student1:amount1:student2:amount2:since[:rent].
Состав пары должен совпасть полностью; since — дата YYYY-MM-DD включительно.
Не меняет цену соло, другой пары или занятий до даты соглашения.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bot.models import Lesson
from bot.models.enums import LessonType
from config.settings import settings


@dataclass(frozen=True)
class PairPrice:
    amounts: dict[str, int]
    rent: int | None = None


def agreement_for(lesson: Lesson) -> PairPrice | None:
    if lesson.type != LessonType.INDIVIDUAL:
        return None
    ids = [s for s in (lesson.student_1_id, lesson.student_2_id, lesson.student_3_id, lesson.student_4_id) if s]
    if len(ids) != 2 or len(set(ids)) != 2:
        return None
    matches: list[tuple[str, PairPrice]] = []
    for chunk in settings.pair_lesson_prices.replace("|", ",").split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) not in (6, 7):
            continue
        teacher, first, amount1, second, amount2, since = parts[:6]
        if (teacher != lesson.teacher_id or {first, second} != set(ids)
                or not amount1.isdigit() or not amount2.isdigit()):
            continue
        try:
            date.fromisoformat(since)
        except ValueError:
            continue
        if since > lesson.date or (len(parts) == 7 and not parts[6].isdigit()):
            continue
        price = PairPrice({first: int(amount1), second: int(amount2)}, int(parts[6]) if len(parts) == 7 else None)
        if price.rent is not None and price.rent > sum(price.amounts.values()):
            continue
        matches.append((since, price))
    return max(matches, key=lambda row: row[0])[1] if matches else None
