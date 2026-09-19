"""
Расчёт сумм по занятиям. Чистые функции, без обращения к БД.
Billing-строки больше не хранятся: счёт ученика и зарплата педагога считаются on-demand.
"""
from __future__ import annotations

from bot.models import Lesson, Billing, Teacher
from bot.models.enums import LessonType
from bot.utils import parse_attendees
from bot.utils.constants import MINUTES_PER_UNIT
from bot.services.rate_history import effective_rates


def calc_earned(
    lesson_type: LessonType, duration_min: int, teacher: Teacher,
    group_id: str = "", attendees: str | None = None, period: str | None = None,
) -> int:
    """earned = ставка × (duration_min / 45). Количество учеников не влияет.

    Ставка — из карточки педагога на месяц занятия, но у групп из
    GROUP_SALARY_RATES своя (напр. «БП Джаз» 1500 ₽/45 мин → 2000 ₽ за час).
    Исключение — группы из REVENUE_SHARE_GROUPS (например, индивидуальные
    Яковлевой): педагог получает процент от сбора с посетивших занятие,
    длительность на зарплату не влияет.
    """
    from config.settings import settings
    share = settings.revenue_share_group_map.get(group_id) if group_id else None
    if share is not None and lesson_type == LessonType.GROUP:
        revenue = sum(
            e.amount for e in parse_attendees(attendees or "", default_duration=duration_min)
        )
        return round(revenue * share / 100)
    # Группы смены (SHIFT_GROUPS): зарплата считается за день целиком (salary_service).
    if group_id and lesson_type == LessonType.GROUP and group_id in settings.shift_group_map:
        return 0
    # Фиксированная зарплатная длительность группы (SALARY_DURATION_GROUPS):
    # пересекающиеся по времени группы (ВБ ХГ Средняя/Старшая) считаются по 90 мин.
    if group_id and lesson_type == LessonType.GROUP:
        duration_min = settings.salary_duration_group_map.get(group_id, duration_min)
    # Прямая оплата педагогу (DIRECT_PAY_TEACHER_IDS): школа зарплату не платит.
    if lesson_type == LessonType.INDIVIDUAL and teacher.teacher_id in settings.direct_pay_teacher_id_set:
        return 0
    # Ставки на период занятия (история ставок: старые цены для прошлых месяцев)
    rate_group, rate_for_teacher, _ = effective_rates(teacher, period)
    rate = rate_group if lesson_type == LessonType.GROUP else rate_for_teacher
    # Своя ставка группы (GROUP_SALARY_RATES) — вместо ставки из карточки педагога.
    if group_id and lesson_type == LessonType.GROUP:
        rate = settings.group_salary_rate_map.get(group_id, rate)
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
            group_id=lesson.group_id or None,
        )

    if lesson.type == LessonType.GROUP:
        if not lesson.attendees:
            return []
        from config.settings import settings
        # Занятия revenue-share групп (индивидуальные Яковлевой) в счетах
        # показываются как индивидуальные, а не групповые посещения.
        row_type = (
            "individual" if lesson.group_id in settings.revenue_share_group_map
            else lesson.type.value
        )
        for entry in parse_attendees(lesson.attendees, default_duration=lesson.duration_min):
            if entry.amount > 0:
                row = _make(entry.student_id, "", entry.amount, entry.duration_min)
                row.lesson_type = row_type
                rows.append(row)
        return rows

    # Индивидуальные занятия педагога с прямой оплатой — не начисляются школой.
    from config.settings import settings
    if lesson.teacher_id in settings.direct_pay_teacher_id_set:
        return rows

    _, _, rate_for_student = effective_rates(teacher, lesson.date[:7])
    base_amount = round(rate_for_student * lesson.duration_min / MINUTES_PER_UNIT)

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
