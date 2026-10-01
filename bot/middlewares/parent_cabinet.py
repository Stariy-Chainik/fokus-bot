"""Родитель в Telegram работает только через кабинет (PARENT_CABINET_ONLY, решение владельца 01.10.2026).

Старые кнопки счетов, оплаты, занятий и дневника (в чатах остались сообщения с ними) и файлы, присланные
в чат, ничего не делают, а отвечают подсказкой и меню с кнопкой «Открыть кабинет». Администраторов и педагогов
не трогаем — у них те же callback'и служат подтверждению оплат. MAX не затрагивается."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

BLOCKED_CALLBACKS = (
    "client:my_bills", "client:lessons", "client:diary",
    "cl_bills_more:", "cl_bills_stu:", "cl_calendar_s:", "cl_date:", "cl_month:", "cl_month_list_s:",
    "cl_month_t:", "cl_nav:", "cl_pick:", "cl_stu:", "cldiary:",
    "client_bill:", "client_pay:", "pay_method:", "cash_notify:", "receipt_upload:", "rcpick:",
    "pselgo", "pselt:", "plsel:", "plsn:", "plsngo", "plsnall",
)
HINT_ALERT = "Счета и оплата теперь в кабинете. Нажмите «📱 Открыть кабинет» в меню бота."
HINT_FILE = ("📎 Чек прикрепляется в кабинете: нажмите «📱 Открыть кабинет», откройте счёт → «Оплатить» → "
             "«По реквизитам» → «Прикрепить чек». Файл, отправленный в чат, администратор не получит.")


def is_blocked_callback(data: str | None) -> bool:
    return bool(data) and data.startswith(BLOCKED_CALLBACKS)     # type: ignore[union-attr]


class ParentCabinetOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("user")
        if user is not None and (user.is_admin or user.teacher_id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and is_blocked_callback(event.data):
            await _redirect(event.message, event.answer, HINT_ALERT, alert=True)
            return None
        if isinstance(event, Message) and (event.photo or event.document):
            await _redirect(event, None, HINT_FILE)
            return None
        return await handler(event, data)


async def _redirect(message, answer, text: str, alert: bool = False) -> None:
    from bot.keyboards.client import kb_client_menu
    try:
        if answer is not None:
            await answer(text, show_alert=alert)
        if message is not None and hasattr(message, "answer"):
            await message.answer(HINT_FILE if not alert else "📱 Всё по оплате и занятиям — в кабинете:",
                                 reply_markup=kb_client_menu())
    except TelegramAPIError as exc:
        logger.warning("Подсказка про кабинет не отправлена: %s", exc)
