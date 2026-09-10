from __future__ import annotations
import logging

from bot.services.rate_history import RateRow
from .base import BaseRepository

logger = logging.getLogger(__name__)


def _int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


class TeacherRateHistoryRepository(BaseRepository):
    """Лист teacher_rate_history — старые ставки педагогов по месяц включительно."""

    async def get_all(self) -> list[RateRow]:
        rows = []
        for r in await self._all_records():
            tid = str(r.get("teacher_id") or "").strip()
            until = str(r.get("until_period") or "").strip()
            if not tid or len(until) != 7:
                continue
            rows.append(RateRow(
                teacher_id=tid, until_period=until,
                rate_group=_int(r.get("rate_group")),
                rate_for_teacher=_int(r.get("rate_for_teacher")),
                rate_for_student=_int(r.get("rate_for_student")),
            ))
        return rows
