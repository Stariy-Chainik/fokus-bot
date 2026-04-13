from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.utils.dates import display_period

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_stats")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


def _period_buttons() -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"stats_period:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "teacher:my_stats")
async def cb_my_stats(callback: CallbackQuery, user: User | None) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "<b>Моя статистика</b>\nВыберите период:", reply_markup=_period_buttons(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stats_period:"))
async def cb_stats_period(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    period_month = callback.data.split(":", 1)[1]
    teacher_id = user.teacher_id

    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    total = len(lessons)
    group_count = sum(1 for ls in lessons if ls.type == LessonType.GROUP)
    ind_count = total - group_count

    submission = await submission_repo.get_by_teacher_and_period(teacher_id, period_month)
    status_line = "🔒 Период сдан на оплату" if submission else "✏️ Период открыт (можно править)"

    lines = [
        f"<b>Статистика за {display_period(period_month)}</b>",
        "",
        f"Занятий: {total} (груп.: {group_count}, инд.: {ind_count})",
        "",
        status_line,
    ]

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_stats")]
        ]),
    )
    await callback.answer()
