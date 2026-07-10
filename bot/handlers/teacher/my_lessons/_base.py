from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.access import (
    is_teacher as _is_teacher,
    is_teacher_or_admin as _is_teacher_or_admin,
)
from bot.models import User
from bot.repositories import TeacherPeriodSubmissionRepository
from bot.utils.dates import month_name_ru

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_lessons")


def _can_view_lesson(user: User | None, lesson) -> bool:
    if user is None:
        return False
    if user.is_admin:
        return True
    return user.teacher_id is not None and lesson.teacher_id == user.teacher_id


def _month_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{month_name_ru(int(month))} {year}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _date_filter_kb() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"my_lessons_date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"my_lessons_date:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="my_lessons_date:manual")],
        [InlineKeyboardButton(text="📋 За месяц", callback_data="my_lessons_date:month")],
        [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_lessons")],
    ])


def _month_picker_kb() -> InlineKeyboardMarkup:
    today = date.today()
    current_year, current_month = today.year, today.month
    previous_year, previous_month = _shift_month(current_year, current_month, -1)
    current_period = f"{current_year:04d}-{current_month:02d}"
    previous_period = f"{previous_year:04d}-{previous_month:02d}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=_month_label(current_period),
            callback_data=f"my_lessons_month:{current_period}",
        )],
        [InlineKeyboardButton(
            text=_month_label(previous_period),
            callback_data=f"my_lessons_month:{previous_period}",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_lessons")],
    ])


async def _submitted_periods(
    teacher_id: str,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> set[str]:
    submissions = await submission_repo.get_by_teacher(teacher_id)
    return {submission.period_month for submission in submissions}


def _locked_ids(lessons, submitted_periods: set[str]) -> set[str]:
    return {
        lesson.lesson_id
        for lesson in lessons
        if lesson.date[:7] in submitted_periods
    }
