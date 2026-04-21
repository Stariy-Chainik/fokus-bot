"""
Расчёт сумм по занятиям. Чистые функции, без обращения к БД.
Billing-строки больше не хранятся: счёт ученика и зарплата педагога считаются on-demand.
"""
from bot.models import Lesson, Billing, Teacher
from bot.models.enums import LessonType
from bot.utils import parse_attendees


def calc_earned(lesson_type: LessonType, duration_min: int, teacher: Teacher) -> int:
    """earned = ставка × (duration_min / 45). Количество учеников не влияет."""
    rate = teacher.rate_group if lesson_type == LessonType.GROUP else teacher.rate_for_teacher
    return round(rate * duration_min / 45)


def build_billing_rows(lesson: Lesson, teacher: Teacher) -> list[Billing]:
    """
    Строит виртуальные billing-строки.
    Не пишет ничего в БД. billing_id пустой — это computed view.
    Для пары: сумма двух строк точно равна полной стоимости урока.
    Для группы per_visit: строка на каждого ученика с amount>0 из attendees.
    """
    rows: list[Billing] = []

    def _make(sid: str, sname: str, amount: int, duration_min: int) -> Billing:
        return Billing(
            billing_id="",
            lesson_id=lesson.lesson_id,
            student_id=sid,
            student_name=sname,
            teacher_id=lesson.teacher_id,
            teacher_name=lesson.teacher_name,
            date=lesson.date,
            duration_min=duration_min,
            amount=amount,
            period_month=lesson.date[:7],
            payment_id=None,
            created_at=lesson.recorded_at,
            updated_at=lesson.updated_at,
            lesson_type=lesson.type.value,
        )

    if lesson.type == LessonType.GROUP:
        if not lesson.attendees:
            return []
        for entry in parse_attendees(lesson.attendees, default_duration=lesson.duration_min):
            if entry.amount > 0:
                rows.append(_make(
                    entry.student_id, "", entry.amount, entry.duration_min,
                ))
        return rows

    base_amount = round(teacher.rate_for_student * lesson.duration_min / 45)

    if lesson.student_1_id and not lesson.student_2_id:
        rows.append(_make(
            lesson.student_1_id, lesson.student_1_name or "",
            base_amount, lesson.duration_min,
        ))
    elif lesson.student_1_id and lesson.student_2_id:
        half = base_amount // 2
        amount_1 = half + (base_amount - half * 2)
        amount_2 = half
        rows.append(_make(
            lesson.student_1_id, lesson.student_1_name or "",
            amount_1, lesson.duration_min,
        ))
        rows.append(_make(
            lesson.student_2_id, lesson.student_2_name or "",
            amount_2, lesson.duration_min,
        ))

    return rows
