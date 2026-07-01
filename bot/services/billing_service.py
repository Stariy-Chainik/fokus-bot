"""
Расчёт сумм по занятиям. Чистые функции, без обращения к БД.
Billing-строки больше не хранятся: счёт ученика и зарплата педагога считаются on-demand.
"""
from bot.models import Lesson, Billing, Teacher
from bot.models.enums import LessonType
from bot.utils import parse_attendees
from bot.utils.constants import MINUTES_PER_UNIT


def calc_earned(lesson_type: LessonType, duration_min: int, teacher: Teacher) -> int:
    """earned = ставка × (duration_min / 45). Количество учеников не влияет."""
    rate = teacher.rate_group if lesson_type == LessonType.GROUP else teacher.rate_for_teacher
    return round(rate * duration_min / MINUTES_PER_UNIT)


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

    base_amount = round(teacher.rate_for_student * lesson.duration_min / MINUTES_PER_UNIT)

    slots = [
        (lesson.student_1_id, lesson.student_1_name),
        (lesson.student_2_id, lesson.student_2_name),
        (lesson.student_3_id, lesson.student_3_name),
        (lesson.student_4_id, lesson.student_4_name),
    ]
    participants = [(sid, sname or "") for sid, sname in slots if sid]
    if not participants:
        return rows

    n = len(participants)
    per = base_amount // n
    remainder = base_amount - per * n
    for i, (sid, sname) in enumerate(participants):
        amount = per + (remainder if i == 0 else 0)
        rows.append(_make(sid, sname, amount, lesson.duration_min))

    return rows
