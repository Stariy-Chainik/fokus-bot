import logging

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from bot.models import User
from bot.keyboards import kb_mode_select, kb_admin_menu, kb_teacher_menu

logger = logging.getLogger(__name__)
router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None) -> None:
    if user is None:
        await message.answer("Вы не зарегистрированы. Обратитесь к администратору.")
        return

    if user.is_admin and user.teacher_id:
        await message.answer("Выберите режим работы:", reply_markup=kb_mode_select())
        return

    if user.is_admin:
        await message.answer("Добро пожаловать, администратор!\n\nВыберите раздел:", reply_markup=kb_admin_menu())
        return

    if user.teacher_id:
        await message.answer("Добро пожаловать!\n\nВыберите действие:", reply_markup=kb_teacher_menu())
        return

    logger.warning("Пользователь tg_id=%s без роли", message.from_user.id if message.from_user else "?")
    await message.answer("Ваша роль не определена. Обратитесь к администратору.")


@router.callback_query(F.data == "mode:admin")
async def cb_mode_admin(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Меню администратора:", reply_markup=kb_admin_menu())
    await callback.answer()


@router.callback_query(F.data == "mode:teacher")
async def cb_mode_teacher(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Меню педагога:", reply_markup=kb_teacher_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Меню администратора:", reply_markup=kb_admin_menu())
    await callback.answer()


@router.callback_query(F.data == "teacher:menu")
async def cb_teacher_menu(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Меню педагога:", reply_markup=kb_teacher_menu())
    await callback.answer()
