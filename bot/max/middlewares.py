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


# Кнопки родителя, которые в режиме «только кабинет» живут в мини-приложении (MAX_PARENT_CABINET_ONLY)
MAX_BLOCKED_CALLBACKS = (
    "client:my_bills", "client:lessons", "client:family", "client:kgroup", "client:unlink",
    "cl_bills_more:", "cl_bills_stu:", "client_bill:", "client_pay:", "client_payall:", "pay_method:", "payall:",
    "cash_notify:", "receipt_upload:", "receipt_upload_all:", "receipt_force:", "rcpick:", "pselgo", "pselt:",
    "mxl:", "client_family:", "famunl:", "famunl_do:", "famunl_why:", "client_kgroup:", "kgroup_clear:",
    "client_unlink:", "client_unlink_do:",
)
CABINET_HINT = "Счета, оплата и занятия теперь в кабинете — нажмите «📱 Открыть кабинет»."
CABINET_FILE_HINT = ("📎 Чек прикрепляется в кабинете: «📱 Открыть кабинет» → счёт → «Оплатить» → «По реквизитам» → "
                     "«Прикрепить чек». Файл, отправленный в чат, администратор не получит.")


def is_max_blocked(payload) -> bool:
    return bool(payload) and str(payload).startswith(MAX_BLOCKED_CALLBACKS)


def cabinet_rows() -> list:
    from bot.screens.types import maxapp
    return [[maxapp("📱 Открыть кабинет", "parent")]]


class ParentCabinetOnlyMiddleware(BaseMiddleware):
    """Родитель в MAX работает только через мини-приложение (MAX_PARENT_CABINET_ONLY, решение владельца 14.10.2026):
    старые кнопки счетов, оплаты, занятий и «кто привязан» и файлы в чат отвечают подсказкой с кнопкой кабинета.
    Вход по ссылке группы, привязка, «Добавить ребёнка» и уведомления работают как прежде."""

    async def __call__(self, handler, event_object, data):
        from config.settings import settings
        if not settings.max_cabinet_only:
            return await handler(event_object, data)
        from .render import send_screen
        callback = getattr(event_object, "callback", None)
        if callback is not None and is_max_blocked(getattr(callback, "payload", None)):
            try:
                await event_object.ack(notification=CABINET_HINT)
            except Exception:
                pass
            await send_screen(event_object.bot, data["max_uid"], "📱 Всё по оплате и занятиям — в кабинете:", cabinet_rows())
            return None
        message = getattr(event_object, "message", None)
        body = getattr(message, "body", None) if callback is None else None
        if body is not None and getattr(body, "attachments", None):
            repo = data.get("student_repo")
            if repo is not None and await repo.get_by_parent_max_id(data["max_uid"]):
                await send_screen(event_object.bot, data["max_uid"], CABINET_FILE_HINT, cabinet_rows())
                return None
        return await handler(event_object, data)
