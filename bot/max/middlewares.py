"""Middleware MAX-диспетчера: дедуп событий и инъекция зависимостей."""
from __future__ import annotations
import logging
import time

from maxapi.filters.middleware import BaseMiddleware

logger = logging.getLogger(__name__)


class DepsMiddleware(BaseMiddleware):
    """Кладёт репозитории/сервисы Telegram-диспетчера в data — хендлеры MAX получают их
    по имени параметра, как в aiogram. Плюс max_uid (id пользователя MAX)."""

    def __init__(self, deps: dict) -> None:
        self._deps = deps

    async def __call__(self, handler, event_object, data):
        data.update(self._deps)
        try:
            data["max_uid"] = event_object.get_ids()[1]
        except Exception:
            data["max_uid"] = None
        if data.get("max_uid") is None:
            return None  # событие не из диалога с пользователем
        return await handler(event_object, data)


class DedupMiddleware(BaseMiddleware):
    """Повторно доставленные события (mid / callback_id) в течение 60 с не обрабатываем."""

    def __init__(self, ttl: float = 60.0) -> None:
        self._seen: dict[str, float] = {}
        self._ttl = ttl

    def _key(self, event_object) -> str | None:
        cb = getattr(event_object, "callback", None)
        if cb is not None:
            return f"c:{cb.callback_id}"
        msg = getattr(event_object, "message", None)
        if msg is not None and getattr(msg, "body", None) is not None:
            return f"m:{msg.body.mid}"
        return None

    async def __call__(self, handler, event_object, data):
        key = self._key(event_object)
        now = time.monotonic()
        if key:
            if len(self._seen) > 256:
                self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
            if now - self._seen.get(key, 0) < self._ttl:
                logger.debug("MAX: повтор события %s — пропущено", key)
                return None
            self._seen[key] = now
        return await handler(event_object, data)
