from __future__ import annotations
import logging
from dataclasses import replace
from datetime import date

from bot.models import Lesson, Teacher
from bot.models.enums import LessonType
from bot.repositories import (
    LessonRepository, TeacherRepository, TeacherPeriodSubmissionRepository,
    BillingRepository,
)
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
        billing_repo: BillingRepository,
    ) -> None:
        self._lesson_repo = lesson_repo
        self._billing_service = billing_service
        self._submission_repo = submission_repo
        self._teacher_repo = teacher_repo
        self._billing_repo = billing_repo

    async def is_period_submitted(self, teacher_id: str, period_month: str) -> bool:
        sub = await self._submission_repo.get_by_teacher_and_period(teacher_id, period_month)
        return sub is not None

    async def _ensure_not_submitted(self, teacher_id: str, period_month: str) -> None:
        if await self.is_period_submitted(teacher_id, period_month):
            raise PermissionError(f"Период {period_month} уже сдан на оплату")

    # ─── Создание занятий (без расчёта денег) ──────────────────────────────

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
            earned=0,
            recorded_at=now,
            updated_at=now,
            attendees=attendees,
        )

        await self._lesson_repo.add(lesson)
        logger.info("Создано занятие %s teacher=%s date=%s (earned/billing не считаем)",
                    lesson_id, teacher.teacher_id, lesson_date)
        return lesson

    async def create_pair_batch(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        pairs: list[tuple[str, str, str, str]],
    ) -> list[Lesson]:
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

    # ─── Удаление ──────────────────────────────────────────────────────────

    async def delete(self, lesson_id: str) -> bool:
        lesson = await self._lesson_repo.get_by_id(lesson_id)
        if lesson is None:
            logger.warning("Занятие %s не найдено при удалении", lesson_id)
            return False
        await self._ensure_not_submitted(lesson.teacher_id, period_month_from_date(lesson.date))

        # Историческая подстраховка: если у занятия были billing-строки (до рефакторинга)
        # — тоже чистим, чтобы не осталось сирот.
        billing_deleted = await self._billing_service.delete_for_lesson(lesson_id)
        deleted = await self._lesson_repo.delete(lesson_id)
        if deleted:
            logger.info("Удалено занятие %s, billing-строк: %d", lesson_id, billing_deleted)
        return deleted

    # ─── Расчёт периода на сдачу ───────────────────────────────────────────

    async def preview_period(self, teacher_id: str, period_month: str) -> tuple[list[Lesson], Teacher, int]:
        """Готовим сводку для подтверждения сдачи: занятия + сумма earned (расчётная)."""
        teacher = await self._teacher_repo.get_by_id(teacher_id)
        if teacher is None:
            raise ValueError(f"Педагог {teacher_id} не найден")
        lessons = await self._lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
        total_earned = sum(calc_earned(ls.type, ls.duration_min, teacher) for ls in lessons)
        return lessons, teacher, total_earned

    async def finalize_period(self, teacher_id: str, period_month: str) -> tuple[int, int]:
        """Проставляет earned в lessons и создаёт billing для занятий месяца.
        Работает только по тем занятиям, где поля ещё не заполнены (идемпотентно).
        Возвращает (lessons_count, total_earned)."""
        teacher = await self._teacher_repo.get_by_id(teacher_id)
        if teacher is None:
            raise ValueError(f"Педагог {teacher_id} не найден")
        lessons = await self._lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
        if not lessons:
            return 0, 0

        total_earned = 0
        now = now_str()
        for ls in lessons:
            earned = calc_earned(ls.type, ls.duration_min, teacher)
            total_earned += earned

            # Проставляем earned только если ещё не проставлен
            if not ls.earned:
                updated = replace(ls, earned=earned, updated_at=now)
                await self._lesson_repo.update(updated)

            # Создаём billing только для individual-занятий, если ещё не создан
            if ls.type == LessonType.INDIVIDUAL:
                existing = await self._billing_repo.get_by_lesson(ls.lesson_id)
                if not existing:
                    updated_ls = replace(ls, earned=earned) if not ls.earned else ls
                    await self._billing_service.create_for_lesson(updated_ls, teacher)

        logger.info("Финализирован период %s teacher=%s lessons=%d earned=%d",
                    period_month, teacher_id, len(lessons), total_earned)
        return len(lessons), total_earned
