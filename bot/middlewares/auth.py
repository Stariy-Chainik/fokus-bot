from __future__ import annotations
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, CallbackQuery, Message

from bot.repositories import UserRepository

logger = logging.getLogger(__name__)


def _label(event: TelegramObject, tg_user: TgUser | None) -> str | None:
    """Короткая метка апдейта для лога: кто и что прислал (middleware стоит на dp.update)."""
    who = f"[{tg_user.id}]" if tg_user else "[?]"
    cb = getattr(event, "callback_query", None) or (event if isinstance(event, CallbackQuery) else None)
    if cb is not None:
        return f"{who} cb:{cb.data}"
    msg = getattr(event, "message", None) or (event if isinstance(event, Message) else None)
    if msg is not None:
        if msg.text:
            return f"{who} msg:{msg.text[:40]!r}"
        return f"{who} msg:<{msg.content_type}>"
    return None


class AuthMiddleware(BaseMiddleware):
    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        t0 = time.monotonic()
        tg_user: TgUser | None = data.get("event_from_user")
        label = _label(event, tg_user)
        if tg_user:
            try:
                data["user"] = await self._user_repo.get_by_tg_id(tg_user.id)
            except Exception as exc:
                logger.error("AuthMiddleware: ошибка чтения users tg_id=%s: %s", tg_user.id, exc)
                data["user"] = None
        else:
            data["user"] = None
        try:
            return await handler(event, data)
        finally:
            if label:
                logger.info("HANDLER %s total %.0f ms", label, (time.monotonic() - t0) * 1000)
