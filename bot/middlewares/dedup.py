from __future__ import annotations
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update


class DedupUpdateMiddleware(BaseMiddleware):
    """Отбрасывает дубликаты апдейтов, когда Telegram доставляет одно
    и то же сообщение/callback с разными update_id (наблюдалось после
    revoke токена). Ключ — (chat_id, message_id) для message, либо id
    callback_query."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def _key(self, update: Update) -> str | None:
        if update.message is not None:
            chat = update.message.chat
            return f"m:{chat.id}:{update.message.message_id}"
        if update.callback_query is not None:
            return f"c:{update.callback_query.id}"
        return None

    def _gc(self, now: float) -> None:
        if len(self._seen) < 256:
            return
        cutoff = now - self._ttl
        for k in [k for k, t in self._seen.items() if t < cutoff]:
            self._seen.pop(k, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            key = self._key(event)
            now = time.monotonic()
            if key is not None:
                last = self._seen.get(key)
                if last is not None and (now - last) < self._ttl:
                    return None
                self._seen[key] = now
                self._gc(now)
        return await handler(event, data)
