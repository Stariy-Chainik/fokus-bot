from __future__ import annotations
from collections import Counter

from bot.models.enums import LessonType


def format_lesson_breakdown(lessons: list) -> tuple[int, int, str, str]:
    """→ (group_count, ind_count, group_line, ind_line).
    Строки вида '3×45м, 2×60м' или '—' если занятий нет."""
    groups: Counter = Counter()
    inds: Counter = Counter()
    for ls in lessons:
        if ls.type == LessonType.GROUP:
            groups[int(ls.duration_min)] += 1
        else:
            inds[int(ls.duration_min)] += 1

    def _fmt(counter: Counter) -> str:
        if not counter:
            return "—"
        return ", ".join(f"{counter[d]}×{d}м" for d in sorted(counter))

    return sum(groups.values()), sum(inds.values()), _fmt(groups), _fmt(inds)
