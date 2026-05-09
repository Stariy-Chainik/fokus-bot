from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import UserRepository, TeacherRepository, StudentRepository
from bot.keyboards import kb_mode_select, kb_admin_menu, kb_teacher_menu
from bot.keyboards.client import kb_client_menu

logger = logging.getLogger(__name__)
router = Router(name="common")


async def show_card(
    event: CallbackQuery | Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Удалить старое сообщение и отправить карточку снизу — чтобы чат не прыгал вверх."""
    target = event.message if isinstance(event, CallbackQuery) else event
    try:
        await target.delete()
    except Exception:
        pass
    await target.answer(text, reply_markup=reply_markup)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(CommandStart(deep_link=False))
async def cmd_start(
    message: Message, user: User | None,
    user_repo: UserRepository, teacher_repo: TeacherRepository,
    student_repo: StudentRepository,
) -> None:
    tg_id = message.from_user.id

    if user is None or (not user.is_admin and not user.teacher_id):
        students = await student_repo.get_by_parent_tg_id(tg_id)
        if students:
            await message.answer("Добро пожаловать!\n\nВыберите раздел:", reply_markup=kb_client_menu())
            return

    if user is None:
        await message.answer(
            "Добро пожаловать!\n\nВведите фамилию ученика для регистрации:",
        )
        return

    if user.is_admin and user.teacher_id:
        await message.answer("Выберите режим работы:", reply_markup=kb_mode_select())
        return

    if user.is_admin:
        await message.answer("Добро пожаловать, администратор!\n\nВыберите раздел:", reply_markup=kb_admin_menu())
        return

    if user.teacher_id:
        await message.answer("Добро пожаловать!\n\nВыберите действие:", reply_markup=kb_teacher_menu(teacher_id=user.teacher_id))
        return

    # Пользователь зарегистрирован, но teacher_id не привязан.
    # Проверяем — может педагог уже добавлен в таблицу teachers по tg_id.
    teacher = await teacher_repo.get_by_tg_id(message.from_user.id)
    if teacher:
        await user_repo.update_teacher_id(message.from_user.id, teacher.teacher_id)
        logger.info("Авто-привязка teacher_id=%s для tg_id=%s", teacher.teacher_id, message.from_user.id)
        can_switch = bool(user.is_admin)
        await message.answer(
            "Добро пожаловать!\n\nВыберите действие:",
            reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=teacher.teacher_id),
        )
        return

    logger.warning("Пользователь tg_id=%s без роли", message.from_user.id if message.from_user else "?")
    await message.answer("Ожидайте, пока администратор назначит вам роль.")


async def _set_current_role(state: FSMContext, role: str) -> None:
    """Запоминает активную роль (admin/teacher), чтобы Home/menu возвращали туда же."""
    await state.update_data(current_role=role)


async def _get_current_role(state: FSMContext) -> str | None:
    data = await state.get_data()
    return data.get("current_role")


async def _clear_state_preserve_role(state: FSMContext) -> str | None:
    """Очищает FSM, но сохраняет current_role для последующей навигации."""
    role = await _get_current_role(state)
    await state.clear()
    if role:
        await state.update_data(current_role=role)
    return role


async def _show_role_menu(
    send_target, user: User, state: FSMContext, role_hint: str | None,
) -> None:
    """Рендерит главное меню активной роли. send_target — message или callback.message."""
    can_switch = bool(user.is_admin and user.teacher_id)
    # Определяем роль для показа: подсказка → единственная роль → fallback admin.
    if role_hint == "admin" and user.is_admin:
        role = "admin"
    elif role_hint == "teacher" and user.teacher_id:
        role = "teacher"
    elif user.is_admin and not user.teacher_id:
        role = "admin"
    elif user.teacher_id and not user.is_admin:
        role = "teacher"
    elif user.is_admin:
        role = "admin"
    elif user.teacher_id:
        role = "teacher"
    else:
        await send_target.answer("Ожидайте, пока администратор назначит вам роль.") \
            if hasattr(send_target, "answer") else None
        return

    await _set_current_role(state, role)
    if role == "admin":
        text, kb = "Меню администратора:", kb_admin_menu(can_switch_role=can_switch)
    else:
        text, kb = "Меню педагога:", kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id)

    # send_target может быть Message (cmd_menu) или callback.message (edit_text).
    if hasattr(send_target, "edit_text"):
        try:
            await send_target.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await send_target.answer(text, reply_markup=kb)


@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User | None, state: FSMContext) -> None:
    """Быстрый возврат в главное меню из любой точки (включая FSM)."""
    role_hint = await _clear_state_preserve_role(state)
    if user is None:
        await message.answer("Сначала отправьте /start для регистрации.")
        return
    if not user.is_admin and not user.teacher_id:
        await message.answer("Ожидайте, пока администратор назначит вам роль.")
        return
    await _show_role_menu(message, user, state, role_hint)


@router.callback_query(F.data == "mode:admin")
async def cb_mode_admin(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _set_current_role(state, "admin")
    can_switch = bool(user.is_admin and user.teacher_id)
    await callback.message.edit_text(
        "Меню администратора:", reply_markup=kb_admin_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "mode:teacher")
async def cb_mode_teacher(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _set_current_role(state, "teacher")
    can_switch = bool(user.is_admin and user.teacher_id)
    await callback.message.edit_text(
        "Меню педагога:", reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _set_current_role(state, "admin")
    can_switch = bool(user.is_admin and user.teacher_id)
    await callback.message.edit_text(
        "Меню администратора:", reply_markup=kb_admin_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:menu")
async def cb_teacher_menu(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _set_current_role(state, "teacher")
    can_switch = bool(user.is_admin and user.teacher_id)
    await callback.message.edit_text(
        "Меню педагога:", reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data == "go:home")
async def cb_go_home(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    """Быстрый возврат в главное меню активной роли (чистит FSM, сохраняет роль)."""
    role_hint = await _clear_state_preserve_role(state)

    # Клиентская роль
    if user is None or (not user.is_admin and not user.teacher_id):
        students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
        if students:
            await callback.message.edit_text(
                "Выберите раздел:", reply_markup=kb_client_menu(),
            )
            await callback.answer()
            return

    if user is None:
        await callback.message.edit_text("Сначала отправьте /start для регистрации.")
        await callback.answer()
        return
    if not user.is_admin and not user.teacher_id:
        await callback.message.edit_text("Ожидайте, пока администратор назначит вам роль.")
        await callback.answer()
        return
    await _show_role_menu(callback.message, user, state, role_hint)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """Подавляет «часики» на некликабельных кнопках (заголовки календаря и т.п.)."""
    await callback.answer()
