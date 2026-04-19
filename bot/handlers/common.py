from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import UserRepository, TeacherRepository
from bot.keyboards import kb_mode_select, kb_admin_menu, kb_teacher_menu, kb_client_menu

logger = logging.getLogger(__name__)
router = Router(name="common")


def _role_count(user: User) -> int:
    return int(bool(user.is_admin)) + int(bool(user.teacher_id)) + int(bool(user.student_id))


async def _open_default_menu(
    message: Message, user: User,
) -> None:
    """Отправляет пользователю меню по его роли(ям)."""
    roles = _role_count(user)
    if roles >= 2:
        await message.answer(
            "Выберите режим работы:",
            reply_markup=kb_mode_select(
                is_admin=bool(user.is_admin),
                is_teacher=bool(user.teacher_id),
                is_client=bool(user.student_id),
            ),
        )
        return
    if user.is_admin:
        await message.answer("Меню администратора:", reply_markup=kb_admin_menu())
        return
    if user.teacher_id:
        await message.answer("Меню педагога:", reply_markup=kb_teacher_menu())
        return
    if user.student_id:
        await message.answer("Меню ученика:", reply_markup=kb_client_menu())
        return


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None, user_repo: UserRepository, teacher_repo: TeacherRepository) -> None:
    if user is None:
        # Автоматически регистрируем нового пользователя
        tg_user = message.from_user
        try:
            await user_repo.add(tg_id=tg_user.id)
            logger.info("Новый пользователь зарегистрирован: tg_id=%s", tg_user.id)
        except Exception as exc:
            logger.error("Ошибка авторегистрации tg_id=%s: %s", tg_user.id, exc)

        # Уведомляем всех администраторов
        name_parts = [tg_user.first_name or "", tg_user.last_name or ""]
        full_name = " ".join(p for p in name_parts if p).strip() or "—"
        username = f"@{tg_user.username}" if tg_user.username else "нет"
        notify_text = (
            f"🆕 Новый пользователь зарегистрировался:\n\n"
            f"Имя: {full_name}\n"
            f"Username: {username}\n"
            f"Telegram ID: <code>{tg_user.id}</code>\n\n"
            f"Добавьте его как педагога через меню Администратора → Педагоги → Добавить"
        )
        notify_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="➕ Добавить как педагога",
                callback_data=f"add_teacher_prefill:{tg_user.id}",
            )
        ]])
        admins = [u for u in await user_repo.get_all() if u.is_admin]
        for admin in admins:
            try:
                await message.bot.send_message(admin.tg_id, notify_text, reply_markup=notify_kb)
            except Exception:
                pass  # Если администратор недоступен — не прерываем

        await message.answer(
            "Добро пожаловать!\n\n"
            "Вы зарегистрированы. Если у вас есть <b>6-значный код привязки</b> "
            "от администратора — отправьте его сообщением, и вы получите доступ "
            "как ученик.\n\n"
            "Если кода нет — ожидайте, пока администратор назначит вам роль."
        )
        return

    if _role_count(user) >= 2:
        await message.answer(
            "Выберите режим работы:",
            reply_markup=kb_mode_select(
                is_admin=bool(user.is_admin),
                is_teacher=bool(user.teacher_id),
                is_client=bool(user.student_id),
            ),
        )
        return

    if user.is_admin:
        await message.answer("Добро пожаловать, администратор!\n\nВыберите раздел:", reply_markup=kb_admin_menu())
        return

    if user.teacher_id:
        await message.answer("Добро пожаловать!\n\nВыберите действие:", reply_markup=kb_teacher_menu())
        return

    if user.student_id:
        await message.answer("Добро пожаловать!\n\nВыберите действие:", reply_markup=kb_client_menu())
        return

    # Пользователь зарегистрирован, но без ролей.
    # Проверяем — может педагог уже добавлен в таблицу teachers по tg_id.
    teacher = await teacher_repo.get_by_tg_id(message.from_user.id)
    if teacher:
        await user_repo.update_teacher_id(message.from_user.id, teacher.teacher_id)
        logger.info("Авто-привязка teacher_id=%s для tg_id=%s", teacher.teacher_id, message.from_user.id)
        can_switch = bool(user.is_admin)
        await message.answer(
            "Добро пожаловать!\n\nВыберите действие:",
            reply_markup=kb_teacher_menu(can_switch_role=can_switch),
        )
        return

    logger.warning("Пользователь tg_id=%s без роли", message.from_user.id if message.from_user else "?")
    await message.answer(
        "Если у вас есть <b>6-значный код привязки</b> от администратора — "
        "отправьте его сообщением. Иначе ожидайте, пока администратор назначит вам роль."
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User | None, state: FSMContext) -> None:
    """Быстрый возврат в главное меню из любой точки (включая FSM)."""
    await state.clear()
    if user is None:
        await message.answer("Сначала отправьте /start для регистрации.")
        return
    if _role_count(user) >= 2:
        await message.answer(
            "Выберите режим работы:",
            reply_markup=kb_mode_select(
                is_admin=bool(user.is_admin),
                is_teacher=bool(user.teacher_id),
                is_client=bool(user.student_id),
            ),
        )
        return
    can_switch = _role_count(user) >= 2
    if user.is_admin:
        await message.answer(
            "Меню администратора:",
            reply_markup=kb_admin_menu(can_switch_role=can_switch),
        )
        return
    if user.teacher_id:
        await message.answer(
            "Меню педагога:",
            reply_markup=kb_teacher_menu(can_switch_role=can_switch),
        )
        return
    if user.student_id:
        await message.answer(
            "Меню ученика:",
            reply_markup=kb_client_menu(
                is_admin=bool(user.is_admin), is_teacher=bool(user.teacher_id),
            ),
        )
        return
    await message.answer(
        "Если у вас есть 6-значный код привязки — отправьте его сообщением."
    )


@router.callback_query(F.data == "mode:admin")
async def cb_mode_admin(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    can_switch = _role_count(user) >= 2
    await callback.message.edit_text(
        "Меню администратора:", reply_markup=kb_admin_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "mode:teacher")
async def cb_mode_teacher(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    can_switch = _role_count(user) >= 2
    await callback.message.edit_text(
        "Меню педагога:", reply_markup=kb_teacher_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    can_switch = _role_count(user) >= 2
    await callback.message.edit_text(
        "Меню администратора:", reply_markup=kb_admin_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:menu")
async def cb_teacher_menu(callback: CallbackQuery, user: User | None) -> None:
    if user is None or not user.teacher_id:
        await callback.answer("Нет доступа", show_alert=True)
        return
    can_switch = _role_count(user) >= 2
    await callback.message.edit_text(
        "Меню педагога:", reply_markup=kb_teacher_menu(can_switch_role=can_switch),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """Подавляет «часики» на некликабельных кнопках (заголовки календаря и т.п.)."""
    await callback.answer()
