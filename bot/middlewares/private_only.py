"""Обычные обработчики бота работают только в личных чатах (15.10.2026).

У бота выключен режим приватности (видит всю переписку групп). Если его добавить в группу — например, в общую
группу педагогов, чтобы присылать туда объявления, — сообщения и нажатия из групп не доходят до обработчиков:
бот там ничего не отвечает (иначе обработчики родителя приняли бы реплики педагогов за ввод фамилии и т. п.).
Добавление и удаление бота в группе (`my_chat_member`) пропускается — его записывает в журнал `common.py`.
Пилот расписания может сохранить черновик из явно настроенного чата до этого фильтра.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import TelegramObject, Update

from bot.services.schedule_pilot import SchedulePilot, SchedulePilots

logger = logging.getLogger(__name__)


def _chat_of(update: Update):
    for msg in (update.message, update.edited_message):
        if msg is not None:
            return msg.chat
    cb = update.callback_query
    if cb is not None and cb.message is not None:
        return cb.message.chat
    return None


class PrivateChatsOnlyMiddleware(BaseMiddleware):
    def __init__(self, schedule_pilot: SchedulePilot | SchedulePilots | None = None):
        self.schedule_pilot = schedule_pilot

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = _chat_of(event) if isinstance(event, Update) else None
        if chat is not None and chat.type != ChatType.PRIVATE:
            if self.schedule_pilot and isinstance(event, Update) and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                message = event.message or event.edited_message
                if message is not None:
                    try:
                        await self.schedule_pilot.receive(message, event.update_id, edited=event.edited_message is not None)
                    except Exception:
                        logger.exception("Не удалось обновить черновик расписания")
            return None  # обычные обработчики не получают групповые сообщения
        return await handler(event, data)
