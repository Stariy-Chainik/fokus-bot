from __future__ import annotations
from bot.models import StudentGroup
from bot.utils.dates import current_period
from .base import BaseRepository

_JOINED_COL = 3
_LEFT_COL = 4


def _row_to_sg(row: dict) -> StudentGroup:
    return StudentGroup(
        student_id=str(row["student_id"]),
        group_id=str(row["group_id"]),
        joined_period=str(row.get("joined_period") or "").strip(),
        left_period=str(row.get("left_period") or "").strip(),
    )


class StudentGroupRepository(BaseRepository):
    async def get_all(self) -> list[StudentGroup]:
        return [_row_to_sg(r) for r in await self._all_records()]

    async def get_groups_for_student(self, student_id: str, include_left: bool = False) -> list[str]:
        """Группы ученика; include_left=True — вместе с теми, откуда он ушёл (для начислений)."""
        return [sg.group_id for sg in await self.get_all()
                if sg.student_id == student_id and (include_left or sg.is_active)]

    async def get_map_by_student(self) -> dict[str, list[str]]:
        """Все привязки, сгруппированные по student_id. Один round-trip."""
        mapping: dict[str, list[str]] = {}
        for sg in await self.get_all():
            if sg.is_active:
                mapping.setdefault(sg.student_id, []).append(sg.group_id)
        return mapping

    async def get_students_for_group(self, group_id: str, include_left: bool = False) -> list[str]:
        """Состав группы; include_left=True — вместе с ушедшими (для начислений за прошлые месяцы)."""
        return [sg.student_id for sg in await self.get_all()
                if sg.group_id == group_id and (include_left or sg.is_active)]

    async def exists(self, student_id: str, group_id: str) -> bool:
        return any(
            sg.student_id == student_id and sg.group_id == group_id
            for sg in await self.get_all()
        )

    async def get_membership_map(self) -> dict[tuple[str, str], "StudentGroup"]:
        """(student_id, group_id) → строка членства (с месяцами вступления и ухода)."""
        return {(sg.student_id, sg.group_id): sg for sg in await self.get_all()}

    async def add(
        self, student_id: str, group_id: str, joined_period: str | None = None,
    ) -> StudentGroup:
        """joined_period (YYYY-MM) — с какого месяца считать абонемент; по умолчанию текущий."""
        existing = next((sg for sg in await self.get_all()
                         if sg.student_id == student_id and sg.group_id == group_id), None)
        if existing is not None:
            if existing.left_period:  # вернулся — снимаем пометку об уходе
                await self.set_left_period(student_id, group_id, "")
            return existing
        joined = joined_period or current_period()
        await self._append_row([student_id, group_id, joined, ""])
        return StudentGroup(student_id=student_id, group_id=group_id, joined_period=joined)

    async def set_joined_period(self, student_id: str, group_id: str, joined_period: str) -> bool:
        """Изменить месяц вступления (админ правит задним числом)."""
        return await self._set_cell(student_id, group_id, _JOINED_COL, joined_period)

    async def set_left_period(self, student_id: str, group_id: str, left_period: str) -> bool:
        """Пометить уход: с этого месяца абонемент не начисляется. Пусто — вернуть в группу."""
        return await self._set_cell(student_id, group_id, _LEFT_COL, left_period)

    async def _set_cell(self, student_id: str, group_id: str, col: int, value: str) -> bool:
        async with self._locked_row(student_id=student_id, group_id=group_id) as row_idx:
            if row_idx is None:
                return False
            await self._update_cell(row_idx, col, value)
            return True

    async def remove(self, student_id: str, group_id: str) -> bool:
        async with self._locked_row(student_id=student_id, group_id=group_id) as row_idx:
            if row_idx is None:
                return False
            await self._delete_row(row_idx)
            return True

    async def remove_all_for_student(self, student_id: str) -> int:
        return await self._delete_all_where(student_id=student_id)

    async def remove_all_for_group(self, group_id: str) -> int:
        return await self._delete_all_where(group_id=group_id)
