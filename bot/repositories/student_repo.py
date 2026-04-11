from typing import Optional
from bot.models import Student
from bot.utils import generate_student_id
from .base import BaseRepository


def _row_to_student(row: dict) -> Student:
    return Student(
        student_id=str(row["student_id"]),
        name=str(row["name"]),
    )


class StudentRepository(BaseRepository):
    async def get_all(self) -> list[Student]:
        return [_row_to_student(r) for r in await self._all_records()]

    async def get_by_id(self, student_id: str) -> Optional[Student]:
        for s in await self.get_all():
            if s.student_id == student_id:
                return s
        return None

    async def search_by_name(self, prefix: str) -> list[Student]:
        prefix_lower = prefix.lower()
        return [s for s in await self.get_all() if s.name.lower().startswith(prefix_lower)]

    async def add(self, name: str) -> Student:
        existing_ids = [s.student_id for s in await self.get_all()]
        student_id = generate_student_id(existing_ids)
        await self._append_row([student_id, name])
        return Student(student_id=student_id, name=name)

    async def delete(self, student_id: str) -> bool:
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True
