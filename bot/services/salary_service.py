"""Зарплата педагога за период: занятия + смены + ручные корректировки дней.

Смена (SHIFT_GROUPS): связка групп, идущих внахлёст в один вечер, например
Боброво ХГ — Младшая 0–60, Средняя 0–120, Спортивная 60–180 (минуты от
начала смены). Зарплата за день = ставка группы × длина ОБЪЕДИНЕНИЯ интервалов
тех групп, у которых в этот день было занятие. Сами занятия связки в зарплату
не идут (calc_earned → 0), их длительности остаются реальными для родителей.

Корректировка дня (лист salary_day_overrides): нестандартный случай — админ
задаёт минуты смены за конкретную дату; они заменяют расчёт по группам.

Премия (лист teacher_bonuses): сумма сверх зарплаты месяца — строка «Премия: …»
после занятий; в итог дня не входит (bot/services/bonuses.py).

Процент от абонементов (SUBSCRIPTION_SHARE_GROUPS, с месяца из настройки): занятия группы в зарплату
не идут (строки in_share по 0 ₽, в смену не входят), а за месяц — строка «share»: процент от начисленных
абонементов группы (`PaymentService.subscription_revenue_rows`). Вели группу несколько педагогов —
делится по числу проведённых занятий. Как премия, в итог дня не входит.
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
    kind: str          # lesson | in_shift | shift | override | bonus | in_share | share
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
        if ls.type == LessonType.GROUP and settings.subscription_share(ls.group_id, ls.date) is not None:
            lines.append(SalaryLine(date=ls.date, kind="in_share", label="", minutes=ls.duration_min, amount=0,
                                    lesson_id=ls.lesson_id, group_id=ls.group_id))
            continue
        in_shift = ls.type == LessonType.GROUP and ls.group_id in shift_map
        if in_shift:
            present_by_day[ls.date].add(ls.group_id)
        amount = 0 if in_shift else calc_earned(
            ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date,
        )
        lines.append(SalaryLine(
            date=ls.date, kind="in_shift" if in_shift else "lesson",
            label="", minutes=ls.duration_min, amount=amount,
            lesson_id=ls.lesson_id, group_id=ls.group_id or "",
        ))

    for day in sorted(set(present_by_day) | set(overrides)):
        rate_group = effective_rates(teacher, day)[0]
        # своя ставка часа смены (GROUP_SALARY_RATES у групп смены, напр. Боброво 1400 ₽/ч с 10.2026)
        own = [r for r in (settings.group_salary_rate(g, day) for g in sorted(present_by_day.get(day, ()))) if r is not None]
        if own:
            rate_group = max(own)
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


def settings_share(group_id: str, period: str) -> int | None:
    from config.settings import settings
    return settings.subscription_share(group_id, period)


def salary_total(lines: list[SalaryLine]) -> int:
    return sum(line.amount for line in lines)


class SalaryService:
    """Зарплата педагога с учётом смен и корректировок (данные из репозиториев)."""

    def __init__(self, lesson_repo, override_repo=None, bonus_repo=None, subscription_revenue=None) -> None:
        self._lesson_repo = lesson_repo
        self._override_repo = override_repo
        self._bonus_repo = bonus_repo
        # async (YYYY-MM) → [(group_id, имя, учеников, начислено ₽)] — PaymentService.subscription_revenue_rows
        self.subscription_revenue = subscription_revenue

    async def overrides_for(self, teacher_id: str, period: str) -> dict[str, tuple[int, str]]:
        if self._override_repo is None:
            return {}
        return {
            o.date: (o.minutes, o.comment)
            for o in await self._override_repo.get_for_teacher_period(teacher_id, period)
        }

    async def bonus_lines(self, teacher_id: str, month: str) -> list[SalaryLine]:
        """Премии месяца (лист teacher_bonuses) — строками начисления после занятий."""
        if self._bonus_repo is None:
            return []
        return [
            SalaryLine(date=b.created_at[:10] or f"{month}-01", kind="bonus",
                       label=f"Премия: {b.comment}" if b.comment else "Премия", minutes=0, amount=b.amount)
            for b in await self._bonus_repo.get_for_teacher_period(teacher_id, month)
        ]

    async def share_lines(self, teacher_id: str, month: str, lessons: list) -> list[SalaryLine]:
        """Процент от начисленных абонементов групп SUBSCRIPTION_SHARE_GROUPS за месяц.

        Доля педагога — по числу проведённых им занятий группы в месяце (вёл один — весь процент)."""
        mine = {ls.group_id for ls in lessons
                if ls.type == LessonType.GROUP and settings_share(ls.group_id, month) is not None}
        if not mine or self.subscription_revenue is None:
            return []
        revenue = {gid: (name, total) for gid, name, _n, total in await self.subscription_revenue(month)}
        held: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ls in await self._lesson_repo.get_all():
            if ls.group_id in mine and ls.type == LessonType.GROUP and ls.date[:7] == month:
                held[ls.group_id][ls.teacher_id] += 1
        out = []
        for gid in sorted(mine, key=lambda g: revenue.get(g, (g, 0))[0]):
            percent = settings_share(gid, month) or 0
            name, total = revenue.get(gid, (gid, 0))
            part = held[gid][teacher_id] / sum(held[gid].values()) if held[gid] else 1
            amount = round(total * percent / 100 * part)
            label = f"{percent} % от абонементов: {name} — {total:,} ₽".replace(",", " ")
            if part < 1:
                label += f" (ваши {held[gid][teacher_id]} из {sum(held[gid].values())} занятий)"
            out.append(SalaryLine(date=f"{month}-01", kind="share", label=label, minutes=0, amount=amount,
                                  group_id=gid))
        return out

    async def lines_for(self, teacher, period: str) -> list[SalaryLine]:
        """period: YYYY-MM (месяц) или YYYY-MM-DD (день — строки только этой даты, без премий)."""
        month = period[:7]
        lessons = await self._lesson_repo.get_by_teacher_and_period(teacher.teacher_id, month)
        from config.settings import settings
        lines = compute_salary_lines(
            teacher, lessons, await self.overrides_for(teacher.teacher_id, month),
            shift_label=settings.shift_label,
        )
        if len(period) == 10:
            return [line for line in lines if line.date == period]
        return (lines + await self.share_lines(teacher.teacher_id, month, lessons)
                + await self.bonus_lines(teacher.teacher_id, month))

    async def total_for(self, teacher, period: str) -> int:
        return salary_total(await self.lines_for(teacher, period))
