from __future__ import annotations
from typing import Optional
from bot.models import Group, GroupBillingMode
from bot.utils import generate_group_id, now_str
from .base import BaseRepository


# Колонки листа `groups` (1-based):
# 1 group_id | 2 branch_id | 3 name | 4 created_at | 5 updated_at | 6 sort_order
# 7 billing_mode | 8 price_short | 9 duration_short | 10 price_full | 11 duration_full
_BILLING_MODE_COL = 7
_PRICE_SHORT_COL = 8
_DUR_SHORT_COL = 9
_PRICE_FULL_COL = 10
_DUR_FULL_COL = 11


def _int_or(v, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except (ValueError, TypeError):
        return default


def _row_to_group(row: dict) -> Group:
    sort_order = _int_or(row.get("sort_order"), 0)
    mode_raw = str(row.get("billing_mode") or "").strip().lower()
    try:
        billing_mode = GroupBillingMode(mode_raw) if mode_raw else GroupBillingMode.NONE
    except ValueError:
        billing_mode = GroupBillingMode.NONE
    return Group(
        group_id=str(row["group_id"]),
        branch_id=str(row["branch_id"]),
        name=str(row["name"]),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        sort_order=sort_order,
        billing_mode=billing_mode,
        price_short=_int_or(row.get("price_short"), 0),
        duration_short=_int_or(row.get("duration_short"), 35),
        price_full=_int_or(row.get("price_full"), 0),
        duration_full=_int_or(row.get("duration_full"), 60),
    )


class GroupRepository(BaseRepository):
    async def get_all(self) -> list[Group]:
        return [_row_to_group(r) for r in await self._all_records()]

    async def get_by_id(self, group_id: str) -> Optional[Group]:
        for g in await self.get_all():
            if g.group_id == group_id:
                return g
        return None

    async def get_by_branch(self, branch_id: str) -> list[Group]:
        return [g for g in await self.get_all() if g.branch_id == branch_id]

    async def add(self, branch_id: str, name: str) -> Group:
        existing_ids = [g.group_id for g in await self.get_all()]
        group_id = generate_group_id(existing_ids)
        now = now_str()
        await self._append_row([
            group_id, branch_id, name, now, now, 0,
            GroupBillingMode.NONE.value, 0, 35, 0, 60,
        ])
        return Group(
            group_id=group_id, branch_id=branch_id, name=name,
            created_at=now, updated_at=now,
        )

    async def update_name(self, group_id: str, name: str) -> bool:
        records = await self._all_records()
        for i, row in enumerate(records):
            if str(row.get("group_id")) == group_id:
                row_idx = i + 2
                await self._update_cell(row_idx, 3, name)
                await self._update_cell(row_idx, 5, now_str())
                return True
        return False

    async def update_billing(
        self, group_id: str,
        billing_mode: GroupBillingMode,
        price_short: int, duration_short: int,
        price_full: int, duration_full: int,
    ) -> bool:
        row_idx = await self._find_row_index("group_id", group_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _BILLING_MODE_COL, billing_mode.value)
        await self._update_cell(row_idx, _PRICE_SHORT_COL, price_short)
        await self._update_cell(row_idx, _DUR_SHORT_COL, duration_short)
        await self._update_cell(row_idx, _PRICE_FULL_COL, price_full)
        await self._update_cell(row_idx, _DUR_FULL_COL, duration_full)
        await self._update_cell(row_idx, 5, now_str())
        return True

    async def delete(self, group_id: str) -> bool:
        row_idx = await self._find_row_index("group_id", group_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
