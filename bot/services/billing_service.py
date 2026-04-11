"""
Сервис расчёта billing. Формулы строго по ТЗ.
"""
import logging
from dataclasses import dataclass

from bot.models import Lesson, Billing, Teacher
from bot.models.enums import LessonType
from bot.utils import generate_billing_id, now_str, period_month_from_date
from bot.repositories import BillingRepository

logger = logging.getLogger(__name__)


def calc_earned(lesson_type: LessonType, duration_min: int, teacher: Teacher) -> int:
    """earned = ставка × (duration_min / 45). Количество учеников не влияет."""
    rate = teacher.rate_group if lesson_type == LessonType.GROUP else teacher.rate_for_teacher
    return round(rate * duration_min / 45)


def _build_billing_rows(lesson: Lesson, teacher: Teacher, existing_ids: list[str]) -> list[Billing]:
    """
    Строит billing-строки для individual-занятия (без обращения к БД).
    Для пары: сумма двух строк точно равна полной стоимости урока.
    """
    if lesson.type == LessonType.GROUP:
        return []

    now = now_str()
    period = period_month_from_date(lesson.date)
    base_amount = round(teacher.rate_for_student * lesson.duration_min / 45)

    ids_pool = list(existing_ids)

    def next_id() -> str:
        bid = generate_billing_id(ids_pool)
        ids_pool.append(bid)
        return bid

    rows: list[Billing] = []

    if lesson.student_1_id and not lesson.student_2_id:
        rows.append(Billing(
            billing_id=next_id(),
            lesson_id=lesson.lesson_id,
            student_id=lesson.student_1_id,
            student_name=lesson.student_1_name or "",
            teacher_id=lesson.teacher_id,
            teacher_name=lesson.teacher_name,
            date=lesson.date,
            duration_min=lesson.duration_min,
            amount=base_amount,
            period_month=period,
            payment_id=None,
            created_at=now,
            updated_at=now,
        ))

    elif lesson.student_1_id and lesson.student_2_id:
        # При нечётной сумме первый ученик получает лишний рубль
        half = base_amount // 2
        amount_1 = half + (base_amount - half * 2)
        amount_2 = half

        for sid, sname, amount in [
            (lesson.student_1_id, lesson.student_1_name or "", amount_1),
            (lesson.student_2_id, lesson.student_2_name or "", amount_2),
        ]:
            rows.append(Billing(
                billing_id=next_id(),
                lesson_id=lesson.lesson_id,
                student_id=sid,
                student_name=sname,
                teacher_id=lesson.teacher_id,
                teacher_name=lesson.teacher_name,
                date=lesson.date,
                duration_min=lesson.duration_min,
                amount=amount,
                period_month=period,
                payment_id=None,
                created_at=now,
                updated_at=now,
            ))

    return rows


class BillingService:
    def __init__(self, billing_repo: BillingRepository) -> None:
        self._billing_repo = billing_repo

    async def create_for_lesson(self, lesson: Lesson, teacher: Teacher) -> list[Billing]:
        if lesson.type == LessonType.GROUP:
            return []
        existing_ids = await self._billing_repo.get_existing_ids()
        rows = _build_billing_rows(lesson, teacher, existing_ids)
        for row in rows:
            try:
                await self._billing_repo.add(row)
            except Exception as exc:
                logger.error("Ошибка создания billing для lesson_id=%s: %s", lesson.lesson_id, exc)
                raise
        return rows

    async def delete_for_lesson(self, lesson_id: str) -> int:
        count = await self._billing_repo.delete_by_lesson(lesson_id)
        logger.info("Удалено %d billing-строк для lesson_id=%s", count, lesson_id)
        return count

    # Публикуем для diagnostics_service
    @staticmethod
    def build_billing_rows(lesson: Lesson, teacher: Teacher, existing_ids: list[str]) -> list[Billing]:
        return _build_billing_rows(lesson, teacher, existing_ids)
