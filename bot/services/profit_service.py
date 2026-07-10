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

    @property
    def income(self) -> int:
        return sum(row.income for row in self.lessons)

    @property
    def salary(self) -> int:
        return sum(row.salary for row in self.lessons)

    @property
    def profit(self) -> int:
        return self.income - self.salary

    @property
    def margin_percent(self) -> int:
        return round(self.profit / self.income * 100) if self.income else 0


def calculate_profit_lesson(
    lesson: Lesson,
    teacher: Teacher,
) -> ProfitLessonRow | None:
    """Возвращает строку только для тарифицируемого занятия.

    Зарплата экрана «Прибыль» учитывается только у занятия с ненулевой
    клиентской выручкой — это зафиксированное текущее правило экрана.
    """
    income = sum(row.amount for row in build_billing_rows(lesson, teacher))
    if income == 0:
        return None
    return ProfitLessonRow(
        lesson_id=lesson.lesson_id,
        date=lesson.date,
        lesson_type=lesson.type,
        duration_min=lesson.duration_min,
        income=income,
        salary=calc_earned(lesson.type, lesson.duration_min, teacher),
    )


def calculate_teacher_profit(
    teacher: Teacher,
    lessons: list[Lesson],
) -> TeacherProfitRow | None:
    billed_lessons = [
        row for lesson in lessons
        if (row := calculate_profit_lesson(lesson, teacher)) is not None
    ]
    if not billed_lessons:
        return None
    group_count = sum(
        1 for row in billed_lessons if row.lesson_type == LessonType.GROUP
    )
    return TeacherProfitRow(
        teacher_id=teacher.teacher_id,
        teacher_name=teacher.name,
        income=sum(row.income for row in billed_lessons),
        salary=sum(row.salary for row in billed_lessons),
        group_lessons=group_count,
        individual_lessons=len(billed_lessons) - group_count,
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
    )


class ProfitService:
    def __init__(
        self,
        teacher_repo,
        lesson_repo,
        payment_service,
        finance_entry_repo,
    ) -> None:
        self._teacher_repo = teacher_repo
        self._lesson_repo = lesson_repo
        self._payment_service = payment_service
        self._finance_entry_repo = finance_entry_repo

    async def get_lesson_summary(self, period: str) -> ProfitSummary:
        rows: list[TeacherProfitRow] = []
        for teacher in await self._teacher_repo.get_all():
            lessons = await self._lesson_repo.get_by_teacher_and_period(
                teacher.teacher_id,
                period,
            )
            row = calculate_teacher_profit(teacher, lessons)
            if row is not None:
                rows.append(row)
        return ProfitSummary(period=period, teacher_rows=tuple(rows))

    async def get_month_summary(self, period: str) -> ProfitSummary:
        lesson_summary = await self.get_lesson_summary(period)
        raw_subscription_rows = (
            await self._payment_service.subscription_revenue_breakdown(period)
        )
        subscription_rows = tuple(
            SubscriptionProfitRow(group_name, billed_students, income)
            for group_name, billed_students, income in raw_subscription_rows
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
