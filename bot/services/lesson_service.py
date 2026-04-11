import logging
from datetime import date

from bot.models import Lesson, Teacher
from bot.models.enums import LessonType
from bot.utils import generate_lesson_id, now_str
from bot.repositories import LessonRepository
from .billing_service import BillingService, calc_earned

logger = logging.getLogger(__name__)


class LessonService:
    def __init__(self, lesson_repo: LessonRepository, billing_service: BillingService) -> None:
        self._lesson_repo = lesson_repo
        self._billing_service = billing_service

    async def create(
        self,
        teacher: Teacher,
        lesson_type: LessonType,
        lesson_date: str,
        duration_min: int,
        student_1_id: str | None = None,
        student_1_name: str | None = None,
        student_2_id: str | None = None,
        student_2_name: str | None = None,
    ) -> Lesson:
        if date.fromisoformat(lesson_date) > date.today():
            raise ValueError(f"Дата {lesson_date} в будущем — запрещено")

        earned = calc_earned(lesson_type, duration_min, teacher)
        now = now_str()
        existing_ids = await self._lesson_repo.get_existing_ids()
        lesson_id = generate_lesson_id(existing_ids)

        lesson = Lesson(
            lesson_id=lesson_id,
            teacher_id=teacher.teacher_id,
            teacher_name=teacher.name,
            type=lesson_type,
            student_1_id=student_1_id,
            student_1_name=student_1_name,
            student_2_id=student_2_id,
            student_2_name=student_2_name,
            date=lesson_date,
            duration_min=duration_min,
            earned=earned,
            recorded_at=now,
            updated_at=now,
        )

        await self._lesson_repo.add(lesson)
        logger.info("Создано занятие %s teacher=%s date=%s", lesson_id, teacher.teacher_id, lesson_date)

        await self._billing_service.create_for_lesson(lesson, teacher)
        return lesson

    async def delete(self, lesson_id: str) -> bool:
        billing_deleted = await self._billing_service.delete_for_lesson(lesson_id)
        deleted = await self._lesson_repo.delete(lesson_id)
        if deleted:
            logger.info("Удалено занятие %s, billing-строк: %d", lesson_id, billing_deleted)
        else:
            logger.warning("Занятие %s не найдено при удалении", lesson_id)
        return deleted
