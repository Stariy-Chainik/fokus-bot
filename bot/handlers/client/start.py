from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

from bot.models import User
from bot.repositories import StudentRepository, UserRepository
from bot.keyboards.client import kb_client_menu, kb_admin_approve_child
from bot.states import ClientRegStates

logger = logging.getLogger(__name__)
router = Router(name="client_start")


def _surname_matches(student_name: str, query: str) -> bool:
    return student_name.lower().startswith(query.lower())


def _student_buttons(matches: list, cb_prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"{cb_prefix}:{s.student_id}")]
        for s in matches[:10]
    ]
    buttons.append([InlineKeyboardButton(text="❌ Нет в списке", callback_data="client_reg_retry")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Первичная регистрация ────────────────────────────────────────────────────

@router.message(StateFilter(None), F.text)
async def handle_surname_input(
    message: Message,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if user is not None and (user.is_admin or user.teacher_id):
        return

    tg_id = message.from_user.id
    existing = await student_repo.get_by_parent_tg_id(tg_id)
    if existing:
        await message.answer("Выберите раздел:", reply_markup=kb_client_menu())
        return

    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите фамилию ученика (минимум 2 символа):")
        return

    matches = [s for s in await student_repo.get_all() if _surname_matches(s.name, query)]
    if not matches:
        await message.answer(f"Ученик с фамилией <b>{query}</b> не найден.\n\nПопробуйте ещё раз:")
        return

    if len(matches) == 1:
        s = matches[0]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, это мой ребёнок", callback_data=f"client_reg:{s.student_id}")],
            [InlineKeyboardButton(text="❌ Другой ученик", callback_data="client_reg_retry")],
        ])
        await message.answer(f"Нашли: <b>{s.name}</b>\n\nЭто ваш ребёнок?", reply_markup=kb)
    else:
        await message.answer(
            f"Найдено несколько учеников с фамилией <b>{query}</b>. Выберите своего:",
            reply_markup=_student_buttons(matches, "client_reg"),
        )


@router.callback_query(F.data.startswith("client_reg:"))
async def cb_client_reg_confirm(
    callback: CallbackQuery,
    student_repo: StudentRepository,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    tg_id = callback.from_user.id
    existing = await student_repo.get_by_parent_tg_id(tg_id)
    if any(s.student_id == student_id for s in existing):
        await callback.message.edit_text(
            "Вы уже привязаны к этому ученику.\n\nВыберите раздел:",
            reply_markup=kb_client_menu(),
        )
        await callback.answer()
        return

    await student_repo.add_parent_tg_id(student_id, tg_id)
    logger.info("Родитель tg_id=%s привязан к student_id=%s", tg_id, student_id)
    await callback.message.edit_text(
        f"✅ Вы привязаны к ученику <b>{student.name}</b>\n\nВыберите раздел:",
        reply_markup=kb_client_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "client_reg_retry")
async def cb_client_reg_retry(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Введите фамилию ученика:")
    await callback.answer()


# ─── Добавление второго ребёнка (с проверкой администратора) ─────────────────

@router.callback_query(F.data == "client:add_child")
async def cb_add_child(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClientRegStates.adding_child)
    await callback.message.edit_text(
        "Введите фамилию ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data="go:home")],
        ]),
    )
    await callback.answer()


@router.message(ClientRegStates.adding_child, F.text)
async def handle_add_child_surname(
    message: Message,
    state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите фамилию ученика (минимум 2 символа):")
        return

    matches = [s for s in await student_repo.get_all() if _surname_matches(s.name, query)]
    if not matches:
        await message.answer(f"Ученик с фамилией <b>{query}</b> не найден.\n\nПопробуйте ещё раз:")
        return

    if len(matches) == 1:
        s = matches[0]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ {s.name}", callback_data=f"client_add_req:{s.student_id}")],
            [InlineKeyboardButton(text="❌ Другой ученик", callback_data="client_add_retry")],
        ])
        await message.answer(f"Нашли: <b>{s.name}</b>\n\nОтправить заявку администратору?", reply_markup=kb)
    else:
        await message.answer(
            f"Найдено несколько учеников. Выберите нужного:",
            reply_markup=_student_buttons(matches, "client_add_req"),
        )


@router.callback_query(F.data.startswith("client_add_req:"))
async def cb_add_child_request(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    user_repo: UserRepository,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    tg_id = callback.from_user.id
    existing = await student_repo.get_by_parent_tg_id(tg_id)
    if any(s.student_id == student_id for s in existing):
        await state.clear()
        await callback.message.edit_text(
            "Вы уже привязаны к этому ученику.\n\nВыберите раздел:",
            reply_markup=kb_client_menu(),
        )
        await callback.answer()
        return

    await state.clear()

    sender_name = callback.from_user.full_name or str(tg_id)
    notify_text = (
        f"👤 <b>Запрос на привязку</b>\n\n"
        f"Клиент: {sender_name} (<code>{tg_id}</code>)\n"
        f"Ученик: <b>{student.name}</b>"
    )
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id, notify_text,
                reply_markup=kb_admin_approve_child(tg_id, student_id),
            )
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить админу заявку на второго ребёнка tg_id=%s: %s", admin.tg_id, exc)

    logger.info("Запрос на добавление: tg_id=%s → student_id=%s", tg_id, student_id)
    await callback.message.edit_text(
        f"✅ Заявка отправлена администратору.\n\nОжидайте подтверждения.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Меню", callback_data="go:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "client_add_retry")
async def cb_add_child_retry(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Введите фамилию ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data="go:home")],
        ]),
    )
    await callback.answer()
