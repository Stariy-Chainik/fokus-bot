from __future__ import annotations
import logging
from dataclasses import dataclass

from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SalaryDayOverride:
    override_id: str   # SO-XXXXXX
    teacher_id: str
    date: str          # YYYY-MM-DD
    minutes: int
    comment: str
    created_at: str
    created_by: int


def _int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


class SalaryOverrideRepository(BaseRepository):
    """Лист salary_day_overrides: нестандартные дни (минуты смены за дату)."""

    async def get_all(self) -> list[SalaryDayOverride]:
        return [
            SalaryDayOverride(
                override_id=str(r.get("override_id") or ""), teacher_id=str(r.get("teacher_id") or ""),
                date=str(r.get("date") or ""), minutes=_int(r.get("minutes")),
                comment=str(r.get("comment") or ""), created_at=str(r.get("created_at") or ""),
                created_by=_int(r.get("created_by")),
            )
            for r in await self._all_records() if r.get("override_id")
        ]

    async def get_for_teacher_period(self, teacher_id: str, period: str) -> list[SalaryDayOverride]:
        return [o for o in await self.get_all() if o.teacher_id == teacher_id and o.date.startswith(period)]

    async def add(self, teacher_id: str, date: str, minutes: int, comment: str, created_by: int) -> SalaryDayOverride:
        from bot.utils.ids import _next_id
        existing = [o.override_id for o in await self.get_all()]
        # один override на (педагог, дата): старый удаляем
        for o in await self.get_all():
            if o.teacher_id == teacher_id and o.date == date:
                await self.delete(o.override_id)
        o = SalaryDayOverride(_next_id("SO", 6, existing), teacher_id, date, minutes, comment, now_str(), created_by)
        await self._append_row([o.override_id, o.teacher_id, o.date, o.minutes, o.comment, o.created_at, o.created_by])
        return o

    async def delete(self, override_id: str) -> bool:
        idx = await self._find_row_index("override_id", override_id)
        if idx is None:
            return False
        await self._delete_row(idx)
        return True
