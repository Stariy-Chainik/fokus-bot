"""Педагог в Telegram работает только через кабинет (TEACHER_CABINET_ONLY, решение владельца 01.10.2026).

Кнопки прежнего бот-меню педагога (в чатах остались сообщения с ними) отвечают подсказкой и меню с кнопкой
«Войти в кабинет». Разрешено только то, что нужно вне кабинета: подтверждение оплат из сообщений (`pact:`/`pnay:` —
у педагогов со счетами), вход и переключение режима. Администраторов middleware не трогает."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)

ALLOWED_CALLBACKS = ("pact:", "pnay:", "go:home", "mode:", "noop", "glink", "client_reg", "client_add")
HINT = "Работа теперь в кабинете. Нажмите «📱 Войти в кабинет»."


def is_allowed_callback(data: str | None) -> bool:
    return not data or data.startswith(ALLOWED_CALLBACKS)


class TeacherCabinetOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("user")
        if (isinstance(event, CallbackQuery) and user is not None and user.teacher_id and not user.is_admin
                and not is_allowed_callback(event.data)):
            await _redirect(event)
            return None
        return await handler(event, data)


async def _redirect(event: CallbackQuery) -> None:
    from bot.keyboards.teacher import TEACHER_CABINET_TEXT, kb_teacher_menu
    try:
        await event.answer(HINT, show_alert=True)
        if event.message is not None and hasattr(event.message, "answer"):
            await event.message.answer(TEACHER_CABINET_TEXT, reply_markup=kb_teacher_menu())
    except TelegramAPIError as exc:
        logger.warning("Подсказка про кабинет педагогу не отправлена: %s", exc)
