from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.models import User
from bot.keyboards import kb_client_menu

logger = logging.getLogger(__name__)
router = Router(name="client_menu")


def _is_client(user: User | None) -> bool:
    return user is not None and bool(user.student_id)


@router.callback_query(F.data == "mode:client")
async def cb_mode_client(callback: CallbackQuery, user: User | None) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    kb = kb_client_menu(is_admin=user.is_admin, is_teacher=bool(user.teacher_id))
    await callback.message.edit_text("Меню ученика:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "client:menu")
async def cb_client_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    kb = kb_client_menu(is_admin=user.is_admin, is_teacher=bool(user.teacher_id))
    await callback.message.edit_text("Меню ученика:", reply_markup=kb)
    await callback.answer()
