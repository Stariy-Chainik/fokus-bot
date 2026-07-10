from __future__ import annotations
from bot.models import SubscriptionOverride
from bot.utils import now_str
from .base import BaseRepository


def _row_to_override(row: dict) -> SubscriptionOverride:
    sid = str(row.get("student_id") or "").strip()
    return SubscriptionOverride(
        group_id=str(row["group_id"]),
        period_month=str(row["period_month"]),
        student_id=sid or None,
        amount=int(row.get("amount") or 0),
        created_at=str(row.get("created_at") or ""),
    )


class SubscriptionOverrideRepository(BaseRepository):
    """Переопределения цены абонемента: (group_id, period_month, student_id?) → amount.

    student_id пуст = вся группа. Ключ уникален (upsert по ключу).
    """

    async def get_all(self) -> list[SubscriptionOverride]:
        return [_row_to_override(r) for r in await self._all_records()]

    async def get_for_group(self, group_id: str) -> list[SubscriptionOverride]:
        return [o for o in await self.get_all() if o.group_id == group_id]

    async def _row_index(self, group_id: str, period_month: str, student_id: str | None) -> int | None:
        sid = student_id or ""
        records = await self._all_records()
        for i, row in enumerate(records):
            if (str(row.get("group_id")) == group_id
                    and str(row.get("period_month")) == period_month
                    and str(row.get("student_id") or "").strip() == sid):
                return i + 2  # 1-based + заголовок
        return None

    async def upsert(
        self, group_id: str, period_month: str, student_id: str | None, amount: int,
    ) -> SubscriptionOverride:
        values = [group_id, period_month, student_id or "", amount, now_str()]
        row_idx = await self._row_index(group_id, period_month, student_id)
        if row_idx is not None:
            await self._update_row(row_idx, values)
        else:
            await self._append_row(values)
        return SubscriptionOverride(
            group_id=group_id, period_month=period_month,
            student_id=student_id, amount=amount,
        )

    async def delete(self, group_id: str, period_month: str, student_id: str | None) -> bool:
        row_idx = await self._row_index(group_id, period_month, student_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
