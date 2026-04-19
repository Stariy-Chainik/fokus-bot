from __future__ import annotations
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, CallbackQuery, Message

from bot.repositories import UserRepository

logger = logging.getLogger(__name__)


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
        label = None
        if isinstance(event, CallbackQuery):
            label = f"cb:{event.data}"
        elif isinstance(event, Message) and event.text:
            label = f"msg:{event.text[:40]}"

        tg_user: TgUser | None = data.get("event_from_user")
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
