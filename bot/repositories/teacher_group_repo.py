from __future__ import annotations
from bot.models import TeacherGroup
from .base import BaseRepository


def _row_to_tg(row: dict) -> TeacherGroup:
    return TeacherGroup(
        teacher_id=str(row["teacher_id"]),
        group_id=str(row["group_id"]),
    )


class TeacherGroupRepository(BaseRepository):
    async def get_all(self) -> list[TeacherGroup]:
        return [_row_to_tg(r) for r in await self._all_records()]

    async def get_groups_for_teacher(self, teacher_id: str) -> list[str]:
        return [tg.group_id for tg in await self.get_all() if tg.teacher_id == teacher_id]

    async def get_teachers_for_group(self, group_id: str) -> list[str]:
        return [tg.teacher_id for tg in await self.get_all() if tg.group_id == group_id]

    async def exists(self, teacher_id: str, group_id: str) -> bool:
        return any(
            tg.teacher_id == teacher_id and tg.group_id == group_id
            for tg in await self.get_all()
        )

    async def add(self, teacher_id: str, group_id: str) -> TeacherGroup:
        await self._append_row([teacher_id, group_id])
        return TeacherGroup(teacher_id=teacher_id, group_id=group_id)

    async def remove(self, teacher_id: str, group_id: str) -> bool:
        records = await self._all_records()
        for i, row in enumerate(records):
            if (str(row.get("teacher_id")) == teacher_id
                    and str(row.get("group_id")) == group_id):
                await self._delete_row(i + 2)
                return True
        return False

    async def remove_all_for_group(self, group_id: str) -> int:
        records = await self._all_records()
        deleted = 0
        for i in range(len(records) - 1, -1, -1):
            if str(records[i].get("group_id")) == group_id:
                await self._delete_row(i + 2)
                deleted += 1
        return deleted
