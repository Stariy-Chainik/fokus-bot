from typing import Optional
from bot.models import Teacher
from bot.utils import generate_teacher_id
from .base import BaseRepository


def _row_to_teacher(row: dict) -> Teacher:
    return Teacher(
        teacher_id=str(row["teacher_id"]),
        tg_id=int(row["tg_id"]) if row.get("tg_id") else None,
        name=str(row["name"]),
        rate_group=int(row.get("rate_group") or 0),
        rate_for_teacher=int(row.get("rate_for_teacher") or 0),
        rate_for_student=int(row.get("rate_for_student") or 0),
    )


class TeacherRepository(BaseRepository):
    async def get_all(self) -> list[Teacher]:
        return [_row_to_teacher(r) for r in await self._all_records()]

    async def get_by_id(self, teacher_id: str) -> Optional[Teacher]:
        for t in await self.get_all():
            if t.teacher_id == teacher_id:
                return t
        return None

    async def get_by_tg_id(self, tg_id: int) -> Optional[Teacher]:
        for t in await self.get_all():
            if t.tg_id == tg_id:
                return t
        return None

    async def add(
        self,
        tg_id: Optional[int],
        name: str,
        rate_group: int,
        rate_for_teacher: int,
        rate_for_student: int,
    ) -> Teacher:
        existing_ids = [t.teacher_id for t in await self.get_all()]
        teacher_id = generate_teacher_id(existing_ids)
        await self._append_row([
            teacher_id,
            tg_id if tg_id is not None else "",
            name,
            rate_group,
            rate_for_teacher,
            rate_for_student,
        ])
        return Teacher(
            teacher_id=teacher_id,
            tg_id=tg_id,
            name=name,
            rate_group=rate_group,
            rate_for_teacher=rate_for_teacher,
            rate_for_student=rate_for_student,
        )

    async def delete(self, teacher_id: str) -> bool:
        row_idx = await self._find_row_index("teacher_id", teacher_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True

    async def update_rates(
        self,
        teacher_id: str,
        rate_group: int,
        rate_for_teacher: int,
        rate_for_student: int,
    ) -> bool:
        records = await self._all_records()
        for i, row in enumerate(records):
            if str(row.get("teacher_id")) == teacher_id:
                row_idx = i + 2
                await self._update_cell(row_idx, 4, rate_group)
                await self._update_cell(row_idx, 5, rate_for_teacher)
                await self._update_cell(row_idx, 6, rate_for_student)
                return True
        return False
