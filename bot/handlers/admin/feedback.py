"""Ответ руководителя на обращение родителя («💬 Написать руководителю», решение владельца 11.11.2026).

Под сообщением с обращением кнопка «↩️ Ответить» (`fbreply:{id}`): руководитель пишет ответ одним сообщением
(FSM `FeedbackReplyStates.waiting_reply`), он уходит родителю в Telegram или MAX, обращение помечается
отвеченным в листе parent_feedback. Кнопка пропускается `AdminCabinetOnlyMiddleware` (ADMIN_ALLOWED).
"""
from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.filters import AdminOnly
from bot.models import User
from bot.screens.adapters import to_aiogram_markup
from bot.screens.types import cb
from bot.services import parent_feedback
from bot.states.admin_states import FeedbackReplyStates

logger = logging.getLogger(__name__)
router = Router(name="admin_feedback")


@router.callback_query(F.data.startswith("fbreply:"), AdminOnly())
async def cb_feedback_reply(callback: CallbackQuery, user: User, state: FSMContext, feedback_repo=None) -> None:
    feedback_id = callback.data.split(":", 1)[1]
    row = await feedback_repo.get_by_id(feedback_id) if feedback_repo is not None else None
    if row is None:
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    await state.set_state(FeedbackReplyStates.waiting_reply)
    await state.update_data(fb_id=feedback_id)
    note = f"\n\nПрошлый ответ: {row.reply}" if row.answered else ""
    await callback.message.answer(
        f"↩️ Ответ родителю на обращение {feedback_id}:\n«{row.text[:200]}{'…' if len(row.text) > 200 else ''}»{note}"
        "\n\nНапишите ответ одним сообщением — он уйдёт родителю.",
        reply_markup=to_aiogram_markup([[cb("Отмена", "fbreply_cancel")]]),
    )
    await callback.answer()


@router.callback_query(F.data == "fbreply_cancel", AdminOnly())
async def cb_feedback_cancel(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Ответ отменён.")
    await callback.answer()


@router.message(FeedbackReplyStates.waiting_reply, F.text)
async def msg_feedback_reply(message: Message, user: User, state: FSMContext, feedback_repo=None) -> None:
    if user is None or not user.is_admin:
        return
    data = await state.get_data()
    feedback_id = data.get("fb_id", "")
    text = (message.text or "").strip()
    if text.startswith("/"):                               # команда — выход из ввода
        await state.clear()
        return
    row, delivered = await parent_feedback.reply(feedback_repo=feedback_repo, feedback_id=feedback_id, text=text,
                                                 actor=message.from_user.id, tg_bot=message.bot)
    await state.clear()
    if row is None:
        await message.answer("Обращение не найдено или ответ пустой — ответ не отправлен.")
        return
    await message.answer("✅ Ответ отправлен родителю." if delivered
                         else "Ответ записан, но родителю доставить не удалось (бот MAX не запущен или родитель закрыл чат).")
