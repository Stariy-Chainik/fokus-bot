from typing import Optional
from bot.models import Lesson
from bot.models.enums import LessonType
from bot.utils import generate_lesson_id, now_str
from .base import BaseRepository


def _row_to_lesson(row: dict) -> Lesson:
    return Lesson(
        lesson_id=str(row["lesson_id"]),
        teacher_id=str(row["teacher_id"]),
        teacher_name=str(row["teacher_name"]),
        type=LessonType(str(row["type"])),
        student_1_id=str(row["student_1_id"]) if row.get("student_1_id") else None,
        student_1_name=str(row["student_1_name"]) if row.get("student_1_name") else None,
        student_2_id=str(row["student_2_id"]) if row.get("student_2_id") else None,
        student_2_name=str(row["student_2_name"]) if row.get("student_2_name") else None,
        date=str(row["date"]),
        duration_min=int(row["duration_min"]),
        earned=int(row.get("earned") or 0),
        recorded_at=str(row["recorded_at"]),
        updated_at=str(row["updated_at"]),
        attendees=str(row["attendees"]) if row.get("attendees") else None,
    )


class LessonRepository(BaseRepository):
    async def get_all(self) -> list[Lesson]:
        return [_row_to_lesson(r) for r in await self._all_records()]

    async def get_by_id(self, lesson_id: str) -> Optional[Lesson]:
        for ls in await self.get_all():
            if ls.lesson_id == lesson_id:
                return ls
        return None

    async def get_by_teacher(self, teacher_id: str) -> list[Lesson]:
        return [ls for ls in await self.get_all() if ls.teacher_id == teacher_id]

    async def get_by_teacher_and_period(self, teacher_id: str, period_month: str) -> list[Lesson]:
        return [
            ls for ls in await self.get_all()
            if ls.teacher_id == teacher_id and ls.date.startswith(period_month)
        ]

    async def get_existing_ids(self) -> list[str]:
        return [ls.lesson_id for ls in await self.get_all()]

    async def add(self, lesson: Lesson) -> Lesson:
        await self._append_row([
            lesson.lesson_id,
            lesson.teacher_id,
            lesson.teacher_name,
            lesson.type.value,
            lesson.student_1_id or "",
            lesson.student_1_name or "",
            lesson.student_2_id or "",
            lesson.student_2_name or "",
            lesson.date,
            lesson.duration_min,
            lesson.earned,
            lesson.recorded_at,
            lesson.updated_at,
            lesson.attendees or "",
        ])
        return lesson

    async def delete(self, lesson_id: str) -> bool:
        row_idx = await self._find_row_index("lesson_id", lesson_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True

    async def update(self, lesson: Lesson) -> bool:
        row_idx = await self._find_row_index("lesson_id", lesson.lesson_id)
        if row_idx is None:
            return False
        await self._update_row(row_idx, [
            lesson.lesson_id,
            lesson.teacher_id,
            lesson.teacher_name,
            lesson.type.value,
            lesson.student_1_id or "",
            lesson.student_1_name or "",
            lesson.student_2_id or "",
            lesson.student_2_name or "",
            lesson.date,
            lesson.duration_min,
            lesson.earned,
            lesson.recorded_at,
            lesson.updated_at,
            lesson.attendees or "",
        ])
        return True
