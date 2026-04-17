from __future__ import annotations
from typing import Optional
from bot.models import Group
from bot.utils import generate_group_id, now_str
from .base import BaseRepository


def _row_to_group(row: dict) -> Group:
    raw_sort = row.get("sort_order")
    try:
        sort_order = int(raw_sort) if raw_sort else 0
    except (ValueError, TypeError):
        sort_order = 0
    return Group(
        group_id=str(row["group_id"]),
        branch_id=str(row["branch_id"]),
        name=str(row["name"]),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        sort_order=sort_order,
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
        await self._append_row([group_id, branch_id, name, now, now])
        return Group(group_id=group_id, branch_id=branch_id, name=name, created_at=now, updated_at=now)

    async def update_name(self, group_id: str, name: str) -> bool:
        records = await self._all_records()
        for i, row in enumerate(records):
            if str(row.get("group_id")) == group_id:
                row_idx = i + 2
                await self._update_cell(row_idx, 3, name)
                await self._update_cell(row_idx, 5, now_str())
                return True
        return False

    async def delete(self, group_id: str) -> bool:
        row_idx = await self._find_row_index("group_id", group_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
