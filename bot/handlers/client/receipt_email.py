"""Email родителя для фискальных чеков — из меню, в любой момент.

Если email не указан, чек об оплате уходит на почту клуба
(YOOKASSA_RECEIPT_EMAIL) — так что вводить его нужно только тем, кому чек нужен.
"""
from __future__ import annotations

import logging
import re

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.repositories import StudentRepository, ClientRepository
from bot.keyboards.client import kb_client_menu
from bot.states.client_states import ClientEmailStates

logger = logging.getLogger(__name__)
router = Router(name="client_receipt_email")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KB_CANCEL = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="« Отмена", callback_data="go:home")],
])


@router.callback_query(F.data == "client:email")
async def cb_client_email(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, client_repo: ClientRepository,
) -> None:
    tg_id = callback.from_user.id
    if not await student_repo.get_by_parent_tg_id(tg_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    client = await client_repo.get_by_tg_id(tg_id)
    current = f"Сейчас: <b>{client.email}</b>\n\n" if client and client.email else ""
    await state.set_state(ClientEmailStates.waiting_email)
    await callback.message.edit_text(
        f"✉️ <b>Email для чеков</b>\n\n{current}"
        "Если нужен чек об оплате — отправьте свою почту.\n"
        "Если не нужен, ничего вводить не надо: чек по умолчанию уходит на почту клуба.",
        reply_markup=_KB_CANCEL,
    )
    await callback.answer()


@router.message(ClientEmailStates.waiting_email, F.text)
async def on_client_email(
    message: Message, state: FSMContext,
    student_repo: StudentRepository, client_repo: ClientRepository,
) -> None:
    email = (message.text or "").strip()
    if not _EMAIL_RE.match(email):
        await message.answer("Не похоже на email. Отправьте адрес вида name@mail.ru.",
                             reply_markup=_KB_CANCEL)
        return
    tg_id = message.from_user.id
    client = await client_repo.get_by_tg_id(tg_id)
    if client is None:
        client = await client_repo.create(
            name=message.from_user.full_name or str(tg_id),
            created_by_tg_id=tg_id, tg_id=tg_id,
        )
        # Привязываем к карточке детей, у которых клиента ещё нет
        for s in await student_repo.get_by_parent_tg_id(tg_id):
            if not s.client_id:
                await student_repo.set_client_id(s.student_id, client.client_id)
    await client_repo.update_email(client.client_id, email)
    await state.clear()
    logger.info("Email для чеков обновлён: клиент %s", client.client_id)
    await message.answer(f"✅ Email сохранён: <b>{email}</b>. Чеки будут приходить на него.",
                         reply_markup=kb_client_menu())
