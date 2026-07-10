from __future__ import annotations
from bot.models import FinanceEntry
from bot.utils import now_str
from bot.utils.ids import generate_finance_id
from .base import BaseRepository


def _row_to_entry(row: dict) -> FinanceEntry:
    return FinanceEntry(
        entry_id=str(row["entry_id"]),
        period_month=str(row["period_month"]),
        kind=str(row.get("kind") or "income"),
        title=str(row.get("title") or ""),
        amount=int(row.get("amount") or 0),
        created_at=str(row.get("created_at") or ""),
    )


class FinanceEntryRepository(BaseRepository):
    """Ручные доходы/расходы месяца (экран «Прибыль»): турниры, аренда и т.п."""

    async def get_all(self) -> list[FinanceEntry]:
        return [_row_to_entry(r) for r in await self._all_records()]

    async def get_by_period(self, period_month: str) -> list[FinanceEntry]:
        return [e for e in await self.get_all() if e.period_month == period_month]

    async def add(self, period_month: str, kind: str, title: str, amount: int) -> FinanceEntry:
        entry_id = generate_finance_id([e.entry_id for e in await self.get_all()])
        entry = FinanceEntry(
            entry_id=entry_id, period_month=period_month,
            kind=kind, title=title, amount=amount, created_at=now_str(),
        )
        await self._append_row([
            entry.entry_id, entry.period_month, entry.kind,
            entry.title, entry.amount, entry.created_at,
        ])
        return entry

    async def delete(self, entry_id: str) -> bool:
        row_idx = await self._find_row_index("entry_id", entry_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
