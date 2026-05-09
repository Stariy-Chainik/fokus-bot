from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import TeacherRepository
from bot.keyboards.admin import kb_admin_menu, kb_teacher_list
from bot.states import RecordLessonStates

logger = logging.getLogger(__name__)
router = Router(name="admin_record_lesson")


def _date_picker_kb() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"lesson_date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"lesson_date:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="lesson_date:manual")],
        [InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson")],
    ])


@router.callback_query(F.data == "admin:record_lesson")
async def cb_admin_record_lesson(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    teacher_repo: TeacherRepository,
) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.answer("Нет педагогов", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "Выберите педагога:",
        reply_markup=kb_teacher_list(teachers, "admin_rl_tch", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rl_tch:"))
async def cb_admin_rl_teacher(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.clear()
    await state.update_data(proxy_teacher_id=teacher_id)
    await state.set_state(RecordLessonStates.choosing_date)
    await callback.message.edit_text(
        "<b>Отметить занятие</b>\nВыберите дату:",
        reply_markup=_date_picker_kb(),
    )
    await callback.answer()
