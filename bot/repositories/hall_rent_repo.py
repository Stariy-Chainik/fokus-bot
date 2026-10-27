"""Лист hall_rent_payments: аренда зала, полученная школой от педагога с прямой оплатой.

Педагог (DIRECT_PAY_TEACHER_IDS) перечисляет аренду за индивидуальные занятия одной суммой в конце
месяца; администратор отмечает её в кабинете («Зарплаты» → педагог → «✅ Аренда получена»). Несколько
строк на месяц — частями. Сумму месяца считает bot/services/hall_rent.py по занятиям.
Лист создаёт scripts/setup_hall_rent.py.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

import gspread

from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

HEADER = ["rent_id", "teacher_id", "period_month", "amount", "paid_at", "paid_by", "comment"]


@dataclass(frozen=True)
class HallRentPayment:
    rent_id: str         # HR-XXXXXX
    teacher_id: str
    period_month: str    # YYYY-MM — за какой месяц аренда
    amount: int
    paid_at: str         # YYYY-MM-DD HH:MM:SS
    paid_by: int         # tg_id администратора
    comment: str = ""


def _int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def _row(r: dict) -> HallRentPayment:
    return HallRentPayment(
        rent_id=str(r.get("rent_id") or ""), teacher_id=str(r.get("teacher_id") or ""),
        period_month=str(r.get("period_month") or ""), amount=_int(r.get("amount")),
        paid_at=str(r.get("paid_at") or ""), paid_by=_int(r.get("paid_by")), comment=str(r.get("comment") or ""),
    )


class HallRentPaymentRepository(BaseRepository):
    """Лист hall_rent_payments: аренда, полученная от педагогов (scripts/setup_hall_rent.py)."""

    async def get_all(self) -> list[HallRentPayment]:
        try:
            records = await self._all_records()
        except gspread.WorksheetNotFound:          # лист ещё не создан — аренда не сводится, экраны работают
            logger.warning("Лист %s не найден — учёт аренды недоступен (scripts/setup_hall_rent.py)",
                           self._sheet_name)
            return []
        return [_row(r) for r in records if r.get("rent_id")]

    async def get_by_id(self, rent_id: str) -> HallRentPayment | None:
        return next((r for r in await self.get_all() if r.rent_id == rent_id), None)

    async def get_by_period(self, period: str) -> list[HallRentPayment]:
        return [r for r in await self.get_all() if r.period_month == period]

    async def get_for_teacher_period(self, teacher_id: str, period: str) -> list[HallRentPayment]:
        return [r for r in await self.get_by_period(period) if r.teacher_id == teacher_id]

    async def add(self, teacher_id: str, period: str, amount: int, paid_by: int, comment: str = "") -> HallRentPayment:
        from bot.utils.ids import _next_id
        async with self._sheet_lock():                  # номер под замком листа — два тапа не получат один HR-
            existing = [r.rent_id for r in await self.get_all()]
            row = HallRentPayment(_next_id("HR", 6, existing), teacher_id, period, amount, now_str(), paid_by, comment)
            await self._append_row([row.rent_id, row.teacher_id, row.period_month, row.amount,
                                    row.paid_at, row.paid_by, row.comment])
        logger.info("Аренда %s: %s %s %d руб. (%s)", row.rent_id, teacher_id, period, amount, comment)
        return row

    async def delete(self, rent_id: str) -> bool:
        async with self._locked_row(rent_id=rent_id) as idx:
            if idx is None:
                return False
            await self._delete_row(idx)
            return True
