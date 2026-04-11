from bot.models import TeacherStudent
from .base import BaseRepository


def _row_to_ts(row: dict) -> TeacherStudent:
    return TeacherStudent(
        teacher_id=str(row["teacher_id"]),
        student_id=str(row["student_id"]),
    )


class TeacherStudentRepository(BaseRepository):
    async def get_all(self) -> list[TeacherStudent]:
        return [_row_to_ts(r) for r in await self._all_records()]

    async def get_students_for_teacher(self, teacher_id: str) -> list[str]:
        return [ts.student_id for ts in await self.get_all() if ts.teacher_id == teacher_id]

    async def get_teachers_for_student(self, student_id: str) -> list[str]:
        return [ts.teacher_id for ts in await self.get_all() if ts.student_id == student_id]

    async def exists(self, teacher_id: str, student_id: str) -> bool:
        return any(
            ts.teacher_id == teacher_id and ts.student_id == student_id
            for ts in await self.get_all()
        )

    async def add(self, teacher_id: str, student_id: str) -> TeacherStudent:
        await self._append_row([teacher_id, student_id])
        return TeacherStudent(teacher_id=teacher_id, student_id=student_id)

    async def remove(self, teacher_id: str, student_id: str) -> bool:
        """Удаляет только связь, не трогает таблицу students."""
        records = await self._all_records()
        for i, row in enumerate(records):
            if (str(row.get("teacher_id")) == teacher_id
                    and str(row.get("student_id")) == student_id):
                await self._delete_row(i + 2)
                return True
        return False
