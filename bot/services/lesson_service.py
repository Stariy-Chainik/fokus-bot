from __future__ import annotations
import logging
from datetime import date

from bot.models import Group, Lesson, Teacher
from bot.models.enums import GroupBillingMode, LessonType
from bot.repositories import (
    LessonRepository, TeacherRepository, TeacherPeriodSubmissionRepository,
)
from bot.utils import AttendeeEntry, generate_lesson_id, now_str, parse_attendees, period_month_from_date, serialize_attendees

logger = logging.getLogger(__name__)


class LessonService:
    def __init__(
        self,
        lesson_repo: LessonRepository,
        submission_repo: TeacherPeriodSubmissionRepository,
        teacher_repo: TeacherRepository,
        salary_service=None,
    ) -> None:
        self._lesson_repo = lesson_repo
        self._submission_repo = submission_repo
        self._teacher_repo = teacher_repo
        self._salary_service = salary_service

    async def _ensure_not_submitted(self, teacher_id: str, period_month: str) -> None:
        sub = await self._submission_repo.get_by_teacher_and_period(teacher_id, period_month)
        if sub is not None:
            raise PermissionError(f"Период {period_month} уже сдан на оплату")

    # ─── Создание занятий ─────────────────────────────────────────────────

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
        student_3_id: str | None = None,
        student_3_name: str | None = None,
        student_4_id: str | None = None,
        student_4_name: str | None = None,
        attendees: str | None = None,
        group_id: str = "",
        bypass_period_lock: bool = False,
    ) -> Lesson:
        if date.fromisoformat(lesson_date) > date.today():
            raise ValueError(f"Дата {lesson_date} в будущем — запрещено")

        # Гард дублей — только «соло против соло»: пара/микрогруппа и соло с тем же
        # учеником в один день допустимы в любом порядке.
        participants = [sid for sid in (student_1_id, student_2_id, student_3_id, student_4_id) if sid]
        if lesson_type == LessonType.INDIVIDUAL and len(participants) == 1:
            if await self._lesson_repo.individual_lesson_exists(teacher.teacher_id, participants[0], lesson_date):
                raise ValueError("Соло-занятие с этим учеником на выбранную дату уже записано")

        if not bypass_period_lock:
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
            group_id=group_id,
            student_3_id=student_3_id,
            student_3_name=student_3_name,
            student_4_id=student_4_id,
            student_4_name=student_4_name,
        )

        await self._lesson_repo.add(lesson)
        logger.info("Создано занятие %s teacher=%s date=%s",
                    lesson_id, teacher.teacher_id, lesson_date)
        return lesson

    async def create_pair_batch(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        pairs: list[tuple[str, str, str, str]],
        bypass_period_lock: bool = False,
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
                bypass_period_lock=bypass_period_lock,
            )
            created.append(lesson)
        return created

    async def create_shared_individual(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        students: list[tuple[str, str]],
        bypass_period_lock: bool = False,
    ) -> Lesson:
        """Одно INDIVIDUAL-занятие на 2-4 солистов (счёт делится поровну).

        Слоты student_1..4 канонизируются: сортировка по student_id,
        недостающие — None.
        """
        ordered = sorted(students, key=lambda s: s[0])
        ids = [sid for sid, _ in ordered] + [None] * (4 - len(ordered))
        names = [name for _, name in ordered] + [None] * (4 - len(ordered))
        return await self.create(
            teacher=teacher,
            lesson_type=LessonType.INDIVIDUAL,
            lesson_date=lesson_date,
            duration_min=duration_min,
            student_1_id=ids[0], student_1_name=names[0],
            student_2_id=ids[1], student_2_name=names[1],
            student_3_id=ids[2], student_3_name=names[2],
            student_4_id=ids[3], student_4_name=names[3],
            bypass_period_lock=bypass_period_lock,
        )

    async def create_soloist_batch(
        self,
        teacher: Teacher,
        lesson_date: str,
        duration_min: int,
        students: list[tuple[str, str]],
        bypass_period_lock: bool = False,
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
                bypass_period_lock=bypass_period_lock,
            )
            created.append(lesson)
        return created

    # ─── Гость в сохранённом групповом занятии ───────────────────────────

    async def add_guest(self, lesson: Lesson, student_id: str, group: Group | None) -> str | None:
        """Добавить ученика в сохранённое групповое занятие; None — уже отмечен.

        Цена гостя — price_full PER_VISIT-группы, иначе 0. Тариф SHORT ученика не
        учитывается (B7 в docs/FOUND_BUGS.md) — поведение сохранено при переносе из хендлера.
        Возвращает новую строку attendees.
        """
        amount = group.price_full if group and group.billing_mode == GroupBillingMode.PER_VISIT else 0
        existing = parse_attendees(lesson.attendees or "", default_duration=lesson.duration_min)
        if any(entry.student_id == student_id for entry in existing):
            return None
        existing.append(AttendeeEntry(student_id=student_id, duration_min=lesson.duration_min, amount=amount))
        new_attendees = serialize_attendees(existing)
        await self._lesson_repo.update_attendees(lesson.lesson_id, new_attendees)
        return new_attendees

    # ─── Удаление ─────────────────────────────────────────────────────────

    async def delete(self, lesson_id: str, bypass_period_lock: bool = False) -> bool:
        lesson = await self._lesson_repo.get_by_id(lesson_id)
        if lesson is None:
            logger.warning("Занятие %s не найдено при удалении", lesson_id)
            return False
        if not bypass_period_lock:
            await self._ensure_not_submitted(lesson.teacher_id, period_month_from_date(lesson.date))

        deleted = await self._lesson_repo.delete(lesson_id)
        if deleted:
            logger.info("Удалено занятие %s", lesson_id)
        return deleted

    # ─── Сводка периода ──────────────────────────────────────────────────

    async def preview_period(self, teacher_id: str, period_month: str) -> tuple[list[Lesson], Teacher, int]:
        """Готовим сводку для подтверждения сдачи: занятия + сумма earned (расчётная)."""
        teacher = await self._teacher_repo.get_by_id(teacher_id)
        if teacher is None:
            raise ValueError(f"Педагог {teacher_id} не найден")
        lessons = await self._lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
        if self._salary_service is not None:
            total_earned = await self._salary_service.total_for(teacher, period_month)
        else:
            from .salary_service import compute_salary_lines, salary_total
            total_earned = salary_total(compute_salary_lines(teacher, lessons))
        return lessons, teacher, total_earned
