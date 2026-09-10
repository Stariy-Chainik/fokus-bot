from __future__ import annotations
import logging
from typing import Optional

from bot.models.entities import TeacherPayout
from bot.utils.ids import generate_payout_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)


def _int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _row_to_payout(row: dict) -> TeacherPayout:
    return TeacherPayout(
        payout_id=str(row.get("payout_id") or ""),
        teacher_id=str(row.get("teacher_id") or ""),
        period_month=str(row.get("period_month") or ""),
        amount=_int(row.get("amount")),
        paid_at=str(row.get("paid_at") or ""),
        paid_by_tg_id=_int(row.get("paid_by_tg_id")),
        comment=str(row.get("comment") or ""),
    )


class TeacherPayoutRepository(BaseRepository):
    """Лист teacher_payouts: факты выплаты зарплаты педагогам по месяцам."""

    async def get_all(self) -> list[TeacherPayout]:
        return [_row_to_payout(r) for r in await self._all_records() if r.get("payout_id")]

    async def get_by_period(self, period_month: str) -> list[TeacherPayout]:
        return [p for p in await self.get_all() if p.period_month == period_month]

    async def get_by_teacher_period(self, teacher_id: str, period_month: str) -> list[TeacherPayout]:
        return [p for p in await self.get_by_period(period_month) if p.teacher_id == teacher_id]

    async def add(
        self, teacher_id: str, period_month: str, amount: int,
        paid_by_tg_id: int, comment: str = "",
    ) -> TeacherPayout:
        existing = [p.payout_id for p in await self.get_all()]
        payout = TeacherPayout(
            payout_id=generate_payout_id(existing), teacher_id=teacher_id,
            period_month=period_month, amount=amount, paid_at=now_str(),
            paid_by_tg_id=paid_by_tg_id, comment=comment,
        )
        await self._append_row([
            payout.payout_id, payout.teacher_id, payout.period_month, payout.amount,
            payout.paid_at, payout.paid_by_tg_id, payout.comment,
        ])
        logger.info("Выплата %s: %s %s %d руб.", payout.payout_id, teacher_id, period_month, amount)
        return payout
