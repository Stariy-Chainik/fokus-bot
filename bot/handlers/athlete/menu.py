from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.repositories import StudentRepository
from ._base import router


@router.callback_query(F.data == "ath:menu")
async def cb_athlete_menu(
    callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository,
) -> None:
    from bot.handlers.common import show_family_menu, _clear_state_preserve_role
    await _clear_state_preserve_role(state)
    ok = await show_family_menu(callback.message, callback.from_user.id, student_repo, state, "athlete")
    if not ok:
        await callback.answer("Кабинет не привязан. Отправьте /start", show_alert=True)
        return
    await callback.answer()
