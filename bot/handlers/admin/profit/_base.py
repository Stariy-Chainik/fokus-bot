from __future__ import annotations

import logging
from datetime import date

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.access import is_admin as _is_admin
from bot.repositories import LessonRepository, TeacherRepository
from bot.services import ProfitService, ProfitSummary, TeacherProfitRow
from bot.utils.dates import display_period, last_periods

logger = logging.getLogger(__name__)
router = Router(name="admin_profit")


def _period_buttons() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=display_period(period),
            callback_data=f"profit_period:{period}",
        )]
        for period in last_periods(6)
    ]
    buttons.append([
        InlineKeyboardButton(text="📅 За день...", callback_data="profit_day_picker")
    ])
    buttons.append([
        InlineKeyboardButton(text="« Назад", callback_data="admin:menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_profit(title: str, summary: ProfitSummary) -> str:
    """Telegram-представление готового доменного расчёта."""
    incomes = [
        entry for entry in summary.finance_entries if entry.kind == "income"
    ]
    expenses = [
        entry for entry in summary.finance_entries if entry.kind == "expense"
    ]

    lines = [f"<b>📊 {title}</b>", ""]
    if summary.is_empty:
        lines.append("Занятий нет.")
        return "\n".join(lines)
    for row in summary.teacher_rows:
        parts = []
        if row.group_lessons:
            parts.append(f"👥 {row.group_lessons}")
        if row.individual_lessons:
            parts.append(f"👤 {row.individual_lessons}")
        count_label = "  ".join(parts)
        lines.append(f"<b>{row.teacher_name}</b>  {count_label}")
        lines.append(
            f"  Выручка: {row.income} ₽  Зарплата: {row.salary} ₽"
        )
        lines.append(
            f"  Прибыль: <b>{row.profit} ₽</b> ({row.margin_percent}%)"
        )
        lines.append("")
    if summary.subscription_rows:
        lines.append("💳 <b>Абонементы</b>")
        for row in summary.subscription_rows:
            lines.append(
                f"  {row.group_name}: {row.income} ₽ ({row.billed_students} уч.)"
            )
        lines.append(
            f"  <b>Итого абонементы: {summary.subscription_income} ₽</b>"
        )
        lines.append("")
    if incomes:
        lines.append("🏆 <b>Прочие доходы</b>")
        for entry in incomes:
            lines.append(f"  {entry.title}: {entry.amount} ₽")
        lines.append(f"  <b>Итого: {summary.manual_income} ₽</b>")
        lines.append("")
    if expenses:
        lines.append("📉 <b>Расходы</b>")
        for entry in expenses:
            lines.append(f"  {entry.title}: {entry.amount} ₽")
        lines.append(f"  <b>Итого: {summary.manual_expenses} ₽</b>")
        lines.append("")
    lines += [
        "──────────────",
        f"Выручка:    {summary.total_income} ₽",
        f"Зарплата: {summary.salary} ₽",
    ]
    if summary.manual_expenses:
        lines.append(f"Расходы:   {summary.manual_expenses} ₽")
    lines.append(f"<b>Прибыль:  {summary.profit} ₽</b>")
    return "\n".join(lines)


def _profit_keyboard(
    rows: tuple[TeacherProfitRow, ...],
    period: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"🔍 {row.teacher_name}",
            callback_data=f"profit_detail:{row.teacher_id}:{period}",
        )]
        for row in rows if row.income > 0
    ]
    buttons.append([
        InlineKeyboardButton(text="« Назад", callback_data=back_callback)
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _lesson_dates_all(
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
    year: int,
    month: int,
) -> set[date]:
    period = f"{year}-{month:02d}"
    result: set[date] = set()
    for teacher in await teacher_repo.get_all():
        lessons = await lesson_repo.get_by_teacher_and_period(
            teacher.teacher_id,
            period,
        )
        for lesson in lessons:
            try:
                result.add(date.fromisoformat(lesson.date))
            except ValueError:
                pass
    return result


async def _period_view(
    period: str,
    profit_service: ProfitService,
) -> tuple[str, InlineKeyboardMarkup]:
    """Месячный вид: занятия + абонементы + ручные записи."""
    summary = await profit_service.get_month_summary(period)
    text = _format_profit(f"Прибыль за {display_period(period)}", summary)
    keyboard = _profit_keyboard(summary.teacher_rows, period, "profit:view")
    finance_buttons = [
        InlineKeyboardButton(
            text="➕ Доход",
            callback_data=f"fin:add:income:{period}",
        ),
        InlineKeyboardButton(
            text="➕ Расход",
            callback_data=f"fin:add:expense:{period}",
        ),
    ]
    if summary.finance_entries:
        finance_buttons.append(InlineKeyboardButton(
            text="🗑",
            callback_data=f"fin:list:{period}",
        ))
    keyboard.inline_keyboard.insert(
        len(keyboard.inline_keyboard) - 1,
        finance_buttons,
    )
    return text, keyboard
