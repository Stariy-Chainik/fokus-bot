"""Расчёт прибыли, независимый от Telegram-представления.

DTO этого модуля можно использовать одинаково из aiogram-хендлеров и будущего
HTTP API. Денежные формулы остаются чистыми: репозитории нужны только классу
``ProfitService`` для загрузки исходных сущностей.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.models import FinanceEntry, Lesson, Teacher
from bot.models.enums import LessonType

from .billing_service import build_billing_rows, calc_earned


@dataclass(frozen=True)
class ProfitLessonRow:
    lesson_id: str
    date: str
    lesson_type: LessonType
    duration_min: int
    income: int
    salary: int
    rent: int = 0  # выручка — аренда зала, а не оплата ученика
    owner_income: int = 0  # «зарплата» руководителя: не расход, остаётся в прибыли

    @property
    def profit(self) -> int:
        return self.income - self.salary


@dataclass(frozen=True)
class TeacherProfitRow:
    teacher_id: str
    teacher_name: str
    income: int
    salary: int
    group_lessons: int
    individual_lessons: int
    rent: int = 0  # часть выручки, полученная как аренда зала
    rent_lessons: int = 0  # сколько занятий её дали
    owner: bool = False  # руководитель: salary = 0, его заработок — в owner_income
    owner_income: int = 0

    @property
    def profit(self) -> int:
        return self.income - self.salary

    @property
    def margin_percent(self) -> int:
        return round(self.profit / self.income * 100) if self.income else 0


@dataclass(frozen=True)
class SubscriptionProfitRow:
    group_name: str
    billed_students: int
    income: int
    group_id: str = ""


@dataclass(frozen=True)
class ProfitSummary:
    period: str
    teacher_rows: tuple[TeacherProfitRow, ...] = ()
    subscription_rows: tuple[SubscriptionProfitRow, ...] = ()
    finance_entries: tuple[FinanceEntry, ...] = ()

    @property
    def lesson_income(self) -> int:
        return sum(row.income for row in self.teacher_rows)

    @property
    def salary(self) -> int:
        return sum(row.salary for row in self.teacher_rows)

    @property
    def subscription_income(self) -> int:
        return sum(row.income for row in self.subscription_rows)

    @property
    def rent_income(self) -> int:
        return sum(row.rent for row in self.teacher_rows)

    @property
    def rent_lessons(self) -> int:
        return sum(row.rent_lessons for row in self.teacher_rows)

    @property
    def owner_income(self) -> int:
        return sum(row.owner_income for row in self.teacher_rows)

    @property
    def manual_income(self) -> int:
        return sum(
            entry.amount for entry in self.finance_entries
            if entry.kind == "income"
        )

    @property
    def manual_expenses(self) -> int:
        return sum(
            entry.amount for entry in self.finance_entries
            if entry.kind == "expense"
        )

    @property
    def total_income(self) -> int:
        return self.lesson_income + self.subscription_income + self.manual_income

    @property
    def total_expenses(self) -> int:
        return self.salary + self.manual_expenses

    @property
    def profit(self) -> int:
        return self.total_income - self.total_expenses

    @property
    def is_empty(self) -> bool:
        return not self.teacher_rows and not self.subscription_rows and not self.finance_entries


@dataclass(frozen=True)
class TeacherProfitDetail:
    teacher_id: str
    teacher_name: str
    period: str
    lessons: tuple[ProfitLessonRow, ...]
    owner: bool = False

    @property
    def income(self) -> int:
        return sum(row.income for row in self.lessons)

    @property
    def salary(self) -> int:
        return sum(row.salary for row in self.lessons)

    @property
    def owner_income(self) -> int:
        return sum(row.owner_income for row in self.lessons)

    @property
    def profit(self) -> int:
        return self.income - self.salary

    @property
    def margin_percent(self) -> int:
        return round(self.profit / self.income * 100) if self.income else 0


def lesson_rent(lesson: Lesson) -> int:
    """Аренда зала за индивидуальное занятие педагога с прямой оплатой.

    Родители платят такому педагогу напрямую (DIRECT_PAY_TEACHER_IDS), а он
    перечисляет школе фиксированную сумму за зал с каждого урока
    (HALL_RENT_PER_LESSON) — независимо от длительности и числа учеников.
    """
    from config.settings import settings
    if lesson.type != LessonType.INDIVIDUAL:
        return 0
    if lesson.teacher_id not in settings.direct_pay_teacher_id_set:
        return 0
    since = settings.hall_rent_since_period
    if since and lesson.date[:7] < since:
        return 0
    return settings.hall_rent_map.get(lesson.teacher_id, 0)


def is_owner(teacher_id: str) -> bool:
    """Руководитель школы (OWNER_TEACHER_IDS): его зарплата — не расход, а часть прибыли."""
    from config.settings import settings
    return teacher_id in settings.owner_teacher_id_set


def calculate_profit_lesson(
    lesson: Lesson,
    teacher: Teacher,
) -> ProfitLessonRow | None:
    """Возвращает строку только для тарифицируемого занятия.

    Зарплата экрана «Прибыль» учитывается только у занятия с ненулевой
    клиентской выручкой — это зафиксированное текущее правило экрана.
    Занятие без выручки от ученика попадает в расчёт, если приносит школе
    аренду зала (``lesson_rent``).
    """
    income = sum(row.amount for row in build_billing_rows(lesson, teacher))
    rent = lesson_rent(lesson) if income == 0 else 0
    if income == 0 and rent == 0:
        return None
    earned = calc_earned(lesson.type, lesson.duration_min, teacher, lesson.group_id, lesson.attendees, lesson.date[:7])
    owner = is_owner(teacher.teacher_id)
    return ProfitLessonRow(
        lesson_id=lesson.lesson_id,
        date=lesson.date,
        lesson_type=lesson.type,
        duration_min=lesson.duration_min,
        income=income or rent,
        salary=0 if owner else earned,
        rent=rent,
        owner_income=earned if owner else 0,
    )


def calculate_teacher_profit(
    teacher: Teacher,
    lessons: list[Lesson],
    extra_salary: int = 0,
) -> TeacherProfitRow | None:
    """extra_salary — зарплата вне занятий: смены и корректировки дней (salary_service)."""
    billed_lessons = [
        row for lesson in lessons
        if (row := calculate_profit_lesson(lesson, teacher)) is not None
    ]
    if not billed_lessons and extra_salary == 0:
        return None
    group_count = sum(
        1 for row in billed_lessons if row.lesson_type == LessonType.GROUP
    )
    owner = is_owner(teacher.teacher_id)
    return TeacherProfitRow(
        teacher_id=teacher.teacher_id,
        teacher_name=teacher.name,
        income=sum(row.income for row in billed_lessons),
        salary=0 if owner else sum(row.salary for row in billed_lessons) + extra_salary,
        group_lessons=group_count,
        individual_lessons=len(billed_lessons) - group_count,
        rent=sum(row.rent for row in billed_lessons),
        rent_lessons=sum(1 for row in billed_lessons if row.rent),
        owner=owner,
        owner_income=sum(row.owner_income for row in billed_lessons) + extra_salary if owner else 0,
    )


def build_teacher_profit_detail(
    teacher: Teacher,
    lessons: list[Lesson],
    period: str,
) -> TeacherProfitDetail:
    billed_lessons = tuple(
        row for lesson in sorted(lessons, key=lambda item: item.date)
        if (row := calculate_profit_lesson(lesson, teacher)) is not None
    )
    return TeacherProfitDetail(
        teacher_id=teacher.teacher_id,
        teacher_name=teacher.name,
        period=period,
        lessons=billed_lessons,
        owner=is_owner(teacher.teacher_id),
    )


class ProfitService:
    def __init__(
        self,
        teacher_repo,
        lesson_repo,
        payment_service,
        finance_entry_repo,
        salary_service=None,
    ) -> None:
        self._teacher_repo = teacher_repo
        self._lesson_repo = lesson_repo
        self._payment_service = payment_service
        self._finance_entry_repo = finance_entry_repo
        self._salary_service = salary_service

    async def get_lesson_summary(self, period: str) -> ProfitSummary:
        rows: list[TeacherProfitRow] = []
        for teacher in await self._teacher_repo.get_all():
            lessons = await self._lesson_repo.get_by_teacher_and_period(
                teacher.teacher_id,
                period,
            )
            extra = 0
            if self._salary_service is not None:
                extra = sum(
                    line.amount for line in await self._salary_service.lines_for(teacher, period)
                    if line.kind in ("shift", "override")
                )
            row = calculate_teacher_profit(teacher, lessons, extra_salary=extra)
            if row is not None:
                rows.append(row)
        return ProfitSummary(period=period, teacher_rows=tuple(rows))

    async def get_month_summary(self, period: str) -> ProfitSummary:
        lesson_summary = await self.get_lesson_summary(period)
        raw_subscription_rows = (
            await self._payment_service.subscription_revenue_rows(period)
        )
        subscription_rows = tuple(
            SubscriptionProfitRow(group_name, billed_students, income, group_id)
            for group_id, group_name, billed_students, income in raw_subscription_rows
        )
        finance_entries = tuple(
            await self._finance_entry_repo.get_by_period(period)
        )
        return ProfitSummary(
            period=period,
            teacher_rows=lesson_summary.teacher_rows,
            subscription_rows=subscription_rows,
            finance_entries=finance_entries,
        )

    async def get_teacher_detail(
        self,
        teacher_id: str,
        period: str,
    ) -> TeacherProfitDetail | None:
        teacher = await self._teacher_repo.get_by_id(teacher_id)
        if teacher is None:
            return None
        lessons = await self._lesson_repo.get_by_teacher_and_period(
            teacher_id,
            period,
        )
        return build_teacher_profit_detail(teacher, lessons, period)
