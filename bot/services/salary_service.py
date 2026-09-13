"""Зарплата педагога за период: занятия + смены + ручные корректировки дней.

Смена (SHIFT_GROUPS): связка групп, идущих внахлёст в один вечер, например
Боброво ХГ — Начальная 0–60, Средняя 0–120, Спортивная 60–180 (минуты от
начала смены). Зарплата за день = ставка группы × длина ОБЪЕДИНЕНИЯ интервалов
тех групп, у которых в этот день было занятие. Сами занятия связки в зарплату
не идут (calc_earned → 0), их длительности остаются реальными для родителей.

Корректировка дня (лист salary_day_overrides): нестандартный случай — админ
задаёт минуты смены за конкретную дату; они заменяют расчёт по группам.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from bot.models.enums import LessonType
from bot.services.billing_service import calc_earned
from bot.services.rate_history import effective_rates
from bot.utils.constants import MINUTES_PER_UNIT


@dataclass(frozen=True)
class SalaryLine:
    date: str
    kind: str          # lesson | in_shift | shift | override
    label: str
    minutes: int
    amount: int
    lesson_id: str | None = None
    group_id: str = ""


def parse_shift_groups(raw: str) -> dict[str, tuple[int, int]]:
    """'GRP-0021:0-60,GRP-0022:0-120' → {gid: (start, end)} в минутах от начала смены."""
    out: dict[str, tuple[int, int]] = {}
    for chunk in raw.replace("|", ",").split(","):
        if ":" not in chunk or "-" not in chunk:
            continue
        gid, span = chunk.split(":", 1)
        a, b = span.split("-", 1)
        if gid.strip() and a.strip().isdigit() and b.strip().isdigit():
            out[gid.strip()] = (int(a), int(b))
    return out


def shift_minutes(present: set[str], shift_map: dict[str, tuple[int, int]]) -> int:
    """Длина объединения интервалов групп, у которых были занятия."""
    spans = sorted(shift_map[g] for g in present if g in shift_map)
    total = 0
    cur_start: int | None = None
    cur_end: int | None = None
    for a, b in spans:
        if cur_end is None or a > cur_end:
            if cur_end is not None and cur_start is not None:
                total += cur_end - cur_start
            cur_start, cur_end = a, b
        else:
            cur_end = max(cur_end, b)
    if cur_end is not None and cur_start is not None:
        total += cur_end - cur_start
    return total


def compute_salary_lines(
    teacher, lessons: list, overrides: dict[str, tuple[int, str]] | None = None,
    shift_map: dict[str, tuple[int, int]] | None = None, shift_label: str = "Смена",
) -> list[SalaryLine]:
    """Чистая функция: строки зарплаты за набор занятий (обычно — месяц)."""
    from config.settings import settings
    if shift_map is None:
        shift_map = settings.shift_group_map
    overrides = overrides or {}
    lines: list[SalaryLine] = []
    present_by_day: dict[str, set[str]] = defaultdict(set)

    for ls in sorted(lessons, key=lambda x: x.date):
        in_shift = ls.type == LessonType.GROUP and ls.group_id in shift_map
        if in_shift:
            present_by_day[ls.date].add(ls.group_id)
        amount = 0 if in_shift else calc_earned(
            ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date[:7],
        )
        lines.append(SalaryLine(
            date=ls.date, kind="in_shift" if in_shift else "lesson",
            label="", minutes=ls.duration_min, amount=amount,
            lesson_id=ls.lesson_id, group_id=ls.group_id or "",
        ))

    for day in sorted(set(present_by_day) | set(overrides)):
        rate_group = effective_rates(teacher, day[:7])[0]
        if day in overrides:
            minutes, comment = overrides[day]
            kind, label = "override", f"Корректировка: {comment}" if comment else "Корректировка дня"
        else:
            minutes = shift_minutes(present_by_day[day], shift_map)
            kind, label = "shift", f"{shift_label}"
        hours = f"{minutes // 60} ч" + (f" {minutes % 60} мин" if minutes % 60 else "")
        lines.append(SalaryLine(
            date=day, kind=kind, label=f"{label} — {hours}", minutes=minutes,
            amount=round(rate_group * minutes / MINUTES_PER_UNIT),
        ))
    return lines


def salary_total(lines: list[SalaryLine]) -> int:
    return sum(line.amount for line in lines)


class SalaryService:
    """Зарплата педагога с учётом смен и корректировок (данные из репозиториев)."""

    def __init__(self, lesson_repo, override_repo=None) -> None:
        self._lesson_repo = lesson_repo
        self._override_repo = override_repo

    async def overrides_for(self, teacher_id: str, period: str) -> dict[str, tuple[int, str]]:
        if self._override_repo is None:
            return {}
        return {
            o.date: (o.minutes, o.comment)
            for o in await self._override_repo.get_for_teacher_period(teacher_id, period)
        }

    async def lines_for(self, teacher, period: str) -> list[SalaryLine]:
        """period: YYYY-MM (месяц) или YYYY-MM-DD (день — строки только этой даты)."""
        month = period[:7]
        lessons = await self._lesson_repo.get_by_teacher_and_period(teacher.teacher_id, month)
        from config.settings import settings
        lines = compute_salary_lines(
            teacher, lessons, await self.overrides_for(teacher.teacher_id, month),
            shift_label=settings.shift_label,
        )
        if len(period) == 10:
            lines = [line for line in lines if line.date == period]
        return lines

    async def total_for(self, teacher, period: str) -> int:
        return salary_total(await self.lines_for(teacher, period))
