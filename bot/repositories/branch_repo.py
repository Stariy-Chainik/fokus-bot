from __future__ import annotations
from typing import Optional
from bot.models import Branch
from bot.utils import generate_branch_id, now_str
from .base import BaseRepository


def _row_to_branch(row: dict) -> Branch:
    return Branch(
        branch_id=str(row["branch_id"]),
        name=str(row["name"]),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


class BranchRepository(BaseRepository):
    async def get_all(self) -> list[Branch]:
        return [_row_to_branch(r) for r in await self._all_records()]

    async def get_by_id(self, branch_id: str) -> Optional[Branch]:
        for b in await self.get_all():
            if b.branch_id == branch_id:
                return b
        return None

    async def add(self, name: str) -> Branch:
        existing_ids = [b.branch_id for b in await self.get_all()]
        branch_id = generate_branch_id(existing_ids)
        now = now_str()
        await self._append_row([branch_id, name, now, now])
        return Branch(branch_id=branch_id, name=name, created_at=now, updated_at=now)

    async def update_name(self, branch_id: str, name: str) -> bool:
        records = await self._all_records()
        for i, row in enumerate(records):
            if str(row.get("branch_id")) == branch_id:
                row_idx = i + 2
                await self._update_cell(row_idx, 2, name)
                await self._update_cell(row_idx, 4, now_str())
                return True
        return False

    async def delete(self, branch_id: str) -> bool:
        row_idx = await self._find_row_index("branch_id", branch_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
