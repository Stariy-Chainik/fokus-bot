"""Лист teacher_bonuses: премии педагогам сверх зарплаты за месяц.

Премия — начисление месяца: SalaryService показывает её строкой «Премия: …» после
занятий (кабинет педагога, «Зарплаты», «Выплаты», «Прибыль» — как зарплата). Парная
строка teacher_payouts (`payout_id`) фиксирует выплату — см. bot/services/bonuses.py.
Лист создаёт scripts/setup_teacher_bonuses.py.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

import gspread

from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

HEADER = ["bonus_id", "teacher_id", "period_month", "amount", "comment", "created_at", "created_by", "payout_id"]


@dataclass(frozen=True)
class TeacherBonus:
    bonus_id: str        # BN-XXXXXX
    teacher_id: str
    period_month: str    # YYYY-MM — месяц, к зарплате которого добавлена премия
    amount: int
    comment: str         # за что; видит педагог
    created_at: str      # YYYY-MM-DD HH:MM:SS
    created_by: int
    payout_id: str = ""  # строка teacher_payouts, которой премия выплачена


def _int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def _row(r: dict) -> TeacherBonus:
    return TeacherBonus(
        bonus_id=str(r.get("bonus_id") or ""), teacher_id=str(r.get("teacher_id") or ""),
        period_month=str(r.get("period_month") or ""), amount=_int(r.get("amount")),
        comment=str(r.get("comment") or ""), created_at=str(r.get("created_at") or ""),
        created_by=_int(r.get("created_by")), payout_id=str(r.get("payout_id") or ""),
    )


class TeacherBonusRepository(BaseRepository):
    """Лист teacher_bonuses: премии педагогам (scripts/setup_teacher_bonuses.py)."""

    async def get_all(self) -> list[TeacherBonus]:
        try:
            records = await self._all_records()
        except gspread.WorksheetNotFound:          # лист ещё не создан — премий нет, экраны работают
            logger.warning("Лист %s не найден — премии недоступны (scripts/setup_teacher_bonuses.py)",
                           self._sheet_name)
            return []
        return [_row(r) for r in records if r.get("bonus_id")]

    async def get_by_id(self, bonus_id: str) -> TeacherBonus | None:
        return next((b for b in await self.get_all() if b.bonus_id == bonus_id), None)

    async def get_by_period(self, period: str) -> list[TeacherBonus]:
        return [b for b in await self.get_all() if b.period_month == period]

    async def get_for_teacher_period(self, teacher_id: str, period: str) -> list[TeacherBonus]:
        return [b for b in await self.get_by_period(period) if b.teacher_id == teacher_id]

    async def add(
        self, teacher_id: str, period: str, amount: int, comment: str, created_by: int, payout_id: str = "",
    ) -> TeacherBonus:
        from bot.utils.ids import _next_id
        existing = [b.bonus_id for b in await self.get_all()]
        b = TeacherBonus(_next_id("BN", 6, existing), teacher_id, period, amount, comment, now_str(), created_by, payout_id)
        await self._append_row([b.bonus_id, b.teacher_id, b.period_month, b.amount, b.comment,
                                b.created_at, b.created_by, b.payout_id])
        logger.info("Премия %s: %s %s %d руб. (%s)", b.bonus_id, teacher_id, period, amount, comment)
        return b

    async def delete(self, bonus_id: str) -> bool:
        async with self._locked_row(bonus_id=bonus_id) as idx:
            if idx is None:
                return False
            await self._delete_row(idx)
            return True
