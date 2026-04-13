from __future__ import annotations
import logging
from dataclasses import replace
from datetime import date

from bot.models import Lesson, Teacher
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherRepository, TeacherPeriodSubmissionRepository
from bot.utils import generate_lesson_id, now_str, period_month_from_date
from .billing_service import BillingService, calc_earned

logger = logging.getLogger(__name__)


class LessonService:
    def __init__(
        self,
        lesson_repo: LessonRepository,
        billing_service: BillingService,
        submission_repo: TeacherPeriodSubmissionRepository,
        teacher_repo: TeacherRepository,
    ) -> None:
        self._lesson_repo = lesson_repo
        self._billing_service = billing_service
        self._submission_repo = submission_repo
        self._teacher_repo = teacher_repo

    async def is_period_submitted(self, teacher_id: str, period_month: str) -> bool:
        sub = await self._submission_repo.get_by_teacher_and_period(teacher_id, period_month)
        return sub is not None

    async def _ensure_not_submitted(self, teacher_id: str, period_month: str) -> None:
        if await self.is_period_submitted(teacher_id, period_month):
            raise PermissionError(f"Период {period_month} уже сдан на оплату")

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
        attendees: str | None = None,
    ) -> Lesson:
        if date.fromisoformat(lesson_date) > date.today():
            raise ValueError(f"Дата {lesson_date} в будущем — запрещено")

        await self._ensure_not_submitted(teacher.teacher_id, period_month_from_date(lesson_date))

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
            attendees=attendees,
        )

        await self._lesson_repo.add(lesson)
        logger.info("Создано занятие %s teacher=%s date=%s", lesson_id, teacher.teacher_id, lesson_date)

        try:
            await self._billing_service.create_for_lesson(lesson, teacher)
        except Exception:
            logger.error("Ошибка billing для занятия %s — откат", lesson_id)
            await self._lesson_repo.delete(lesson_id)
            raise

        return lesson

    async def create_pair_batch(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        pairs: list[tuple[str, str, str, str]],
    ) -> list[Lesson]:
        """Создаёт N парных занятий — по одному на пару
        (a_id, a_name, b_id, b_name)."""
        created: list[Lesson] = []
        for a_id, a_name, b_id, b_name in pairs:
            lesson = await self.create(
                teacher=teacher,
                lesson_type=LessonType.INDIVIDUAL,
                lesson_date=lesson_date,
                duration_min=duration_min,
                student_1_id=a_id,
                student_1_name=a_name,
                student_2_id=b_id,
                student_2_name=b_name,
            )
            created.append(lesson)
        return created

    async def create_soloist_batch(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        students: list[tuple[str, str]],
    ) -> list[Lesson]:
        """Создаёт N индивидуальных занятий-соло — по одному на каждого ученика
        из списка (student_id, student_name). Билинг создаётся для каждого."""
        created: list[Lesson] = []
        for sid, sname in students:
            lesson = await self.create(
                teacher=teacher,
                lesson_type=LessonType.INDIVIDUAL,
                lesson_date=lesson_date,
                duration_min=duration_min,
                student_1_id=sid,
                student_1_name=sname,
            )
            created.append(lesson)
        return created

    async def delete(self, lesson_id: str) -> bool:
        lesson = await self._lesson_repo.get_by_id(lesson_id)
        if lesson is None:
            logger.warning("Занятие %s не найдено при удалении", lesson_id)
            return False
        await self._ensure_not_submitted(lesson.teacher_id, period_month_from_date(lesson.date))

        billing_deleted = await self._billing_service.delete_for_lesson(lesson_id)
        deleted = await self._lesson_repo.delete(lesson_id)
        if deleted:
            logger.info("Удалено занятие %s, billing-строк: %d", lesson_id, billing_deleted)
        return deleted

    async def update_and_rebill(self, lesson_id: str, **fields) -> Lesson:
        """Атомарно обновляет поля занятия и при необходимости пересобирает billing.

        Поддерживаемые поля: type, duration_min, date,
        student_1_id, student_1_name, student_2_id, student_2_name, attendees.
        """
        lesson = await self._lesson_repo.get_by_id(lesson_id)
        if lesson is None:
            raise ValueError(f"Занятие {lesson_id} не найдено")
        teacher = await self._teacher_repo.get_by_id(lesson.teacher_id)
        if teacher is None:
            raise ValueError(f"Педагог {lesson.teacher_id} не найден")

        old_period = period_month_from_date(lesson.date)
        await self._ensure_not_submitted(lesson.teacher_id, old_period)

        new_date = fields.get("date", lesson.date)
        if new_date != lesson.date:
            if date.fromisoformat(new_date) > date.today():
                raise ValueError(f"Дата {new_date} в будущем — запрещено")
            new_period = period_month_from_date(new_date)
            if new_period != old_period:
                await self._ensure_not_submitted(lesson.teacher_id, new_period)

        allowed = {
            "type", "duration_min", "date",
            "student_1_id", "student_1_name",
            "student_2_id", "student_2_name",
            "attendees",
        }
        changes = {k: v for k, v in fields.items() if k in allowed}
        updated = replace(lesson, **changes, updated_at=now_str())

        # пересчёт earned если изменились type/duration
        if "type" in changes or "duration_min" in changes:
            updated = replace(updated, earned=calc_earned(updated.type, updated.duration_min, teacher))

        # нужно ли пересоздавать billing
        billing_affecting = {
            "type", "duration_min",
            "student_1_id", "student_1_name",
            "student_2_id", "student_2_name",
        }
        date_changed_cross_month = (
            "date" in changes
            and period_month_from_date(changes["date"]) != old_period
        )
        rebill = any(k in changes for k in billing_affecting) or date_changed_cross_month

        # применяем изменение lesson-строки
        ok = await self._lesson_repo.update(updated)
        if not ok:
            raise ValueError(f"Не удалось обновить занятие {lesson_id}")

        if rebill:
            try:
                await self._billing_service.delete_for_lesson(lesson_id)
                await self._billing_service.create_for_lesson(updated, teacher)
            except Exception:
                logger.error("Ошибка rebill для занятия %s — откат", lesson_id)
                # откат lesson-строки
                await self._lesson_repo.update(lesson)
                # откат billing: пересоздать из старой версии
                try:
                    await self._billing_service.delete_for_lesson(lesson_id)
                    await self._billing_service.create_for_lesson(lesson, teacher)
                except Exception:
                    logger.error("Не удалось восстановить billing для %s", lesson_id)
                raise

        logger.info("Обновлено занятие %s fields=%s", lesson_id, list(changes.keys()))
        return updated
