"""Администратор и спортсмен в Telegram работают только через кабинет (ADMIN_CABINET_ONLY / ATHLETE_CABINET_ONLY,
решение владельца 21.10.2026: «Telegram-бот только как мини-приложение»).

Прежние кнопки бот-меню (в чатах остались сообщения с ними) отвечают подсказкой и кнопкой кабинета. Разрешено
только то, что живёт в уведомлениях и при входе: решения по заявкам родителей (чек, наличные, привязка, новый
ребёнок, отвязка), отвязка спортсмена, напоминания, вход и регистрация. Педагоги и родители — свои middleware."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)

# кнопки уведомлений и входа — работают и в режиме «только кабинет»
ADMIN_ALLOWED = ("pact:", "pnay:", "admin_child_", "nchild_", "unlink_ok:", "unlink_no:", "receipt_confirm:", "rcpt_no:",
                 "rcpp:", "remind:", "athreg:", "go:home", "mode:", "noop", "glink", "client_reg", "client_add")
ADMIN_HINT = "Управление школой теперь в кабинете. Нажмите «📱 Открыть кабинет»."

ATHLETE_BLOCKED = ("ath:", "athent:", "athlog:", "athcal_")
ATHLETE_HINT = "Дневник теперь в приложении. Нажмите «📱 Открыть дневник»."


def admin_callback_allowed(data: str | None) -> bool:
    return not data or data.startswith(ADMIN_ALLOWED)


class AdminCabinetOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("user")
        if isinstance(event, CallbackQuery) and user is not None and user.is_admin \
                and not admin_callback_allowed(event.data):
            from bot.keyboards.admin import ADMIN_CABINET_TEXT, kb_admin_menu
            await _redirect(event, ADMIN_HINT, ADMIN_CABINET_TEXT, kb_admin_menu())
            return None
        return await handler(event, data)


class AthleteCabinetOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith(ATHLETE_BLOCKED):
            from bot.keyboards.athlete import kb_athlete_menu
            await _redirect(event, ATHLETE_HINT, "📱 Дневник спортсмена — в приложении:", kb_athlete_menu())
            return None
        return await handler(event, data)


async def _redirect(event: CallbackQuery, hint: str, text: str, markup) -> None:
    try:
        await event.answer(hint, show_alert=True)
        if event.message is not None and hasattr(event.message, "answer"):
            await event.message.answer(text, reply_markup=markup)
    except TelegramAPIError as exc:
        logger.warning("Подсказка про кабинет не отправлена: %s", exc)
