"""В группах бот молчит: сообщения и нажатия не из личных чатов до обработчиков не доходят (15.10.2026)."""
import asyncio
from datetime import datetime

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.middlewares import PrivateChatsOnlyMiddleware

USER = User(id=1, is_bot=False, first_name="Олеся")


def _msg(chat_type: str) -> Message:
    return Message(message_id=1, date=datetime(2026, 10, 15), chat=Chat(id=-100 if chat_type != "private" else 1, type=chat_type),
                   from_user=USER, text="Коллеги, добрый день")


def _run(update: Update) -> bool:
    called = []

    async def handler(event, data):
        called.append(event)
        return "ok"
    asyncio.run(PrivateChatsOnlyMiddleware()(handler, update, {}))
    return bool(called)


def test_private_passes_group_is_dropped():
    assert _run(Update(update_id=1, message=_msg("private"))) is True
    assert _run(Update(update_id=2, message=_msg("supergroup"))) is False
    assert _run(Update(update_id=3, message=_msg("group"))) is False
    cb = CallbackQuery(id="c", from_user=USER, chat_instance="x", data="go:home", message=_msg("supergroup"))
    assert _run(Update(update_id=4, callback_query=cb)) is False
