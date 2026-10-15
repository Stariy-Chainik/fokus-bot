"""Бот работает только в личных чатах (15.10.2026).

У бота выключен режим приватности (видит всю переписку групп). Если его добавить в группу — например, в общую
группу педагогов, чтобы присылать туда объявления, — сообщения и нажатия из групп не доходят до обработчиков:
бот там ничего не отвечает (иначе обработчики родителя приняли бы реплики педагогов за ввод фамилии и т. п.).
Добавление и удаление бота в группе (`my_chat_member`) пропускается — его записывает в журнал `common.py`.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import TelegramObject, Update


def _chat_of(update: Update):
    for msg in (update.message, update.edited_message):
        if msg is not None:
            return msg.chat
    cb = update.callback_query
    if cb is not None and cb.message is not None:
        return cb.message.chat
    return None


class PrivateChatsOnlyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = _chat_of(event) if isinstance(event, Update) else None
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None                                 # группа или канал — молчим
        return await handler(event, data)
