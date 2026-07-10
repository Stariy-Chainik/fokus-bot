from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin import kb_back
from bot.keyboards.teacher import kb_teacher_menu
from bot.models import User
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.services import LessonService
from bot.utils.dates import format_date_display

from ._base import (
    _can_view_lesson,
    _is_teacher_or_admin,
    _submitted_periods,
    router,
)


@router.callback_query(F.data.startswith("delete_lesson:"))
async def cb_delete_lesson_confirm(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or not _can_view_lesson(user, lesson):
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    if not user.is_admin:
        periods = await _submitted_periods(lesson.teacher_id, submission_repo)
        if lesson.date[:7] in periods:
            await callback.answer(
                "🔒 Период сдан — обратитесь к администратору.",
                show_alert=True,
            )
            return
    await callback.message.edit_text(
        f"Удалить занятие {lesson_id} от {format_date_display(lesson.date)}?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=f"confirm_delete_lesson:{lesson_id}",
            )],
            [InlineKeyboardButton(
                text="« Назад",
                callback_data=f"lesson_detail:{lesson_id}",
            )],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_lesson:"))
async def cb_delete_lesson_do(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    lesson_service: LessonService,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if lesson and not _can_view_lesson(user, lesson):
        await callback.answer("Нет доступа к этому занятию", show_alert=True)
        return
    try:
        deleted = await lesson_service.delete(
            lesson_id,
            bypass_period_lock=bool(user.is_admin),
        )
    except PermissionError:
        await callback.answer(
            "🔒 Период сдан — обратитесь к администратору.",
            show_alert=True,
        )
        return
    text = f"Занятие {lesson_id} удалено." if deleted else "Занятие не найдено."
    back_keyboard = kb_back("admin:edit_lesson") if user.is_admin else kb_teacher_menu()
    await callback.message.edit_text(text, reply_markup=back_keyboard)
    await callback.answer()
