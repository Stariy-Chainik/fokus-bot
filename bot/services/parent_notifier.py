"""Адреса родителей в двух мессенджерах и единая доставка уведомлений.

Addr = ("tg", id) | ("max", id). В callback-строках адрес кодируется `fmt_addr`:
Telegram — голые цифры (как раньше, старые кнопки продолжают работать), MAX — `m<id>`.
Уведомления родителям (счета, напоминания, подтверждения оплаты, оценки) идут через
`ParentNotifier`, который знает про оба бота; MAX-бот может отсутствовать (токен пуст).
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from aiogram.exceptions import TelegramAPIError

from bot.screens.adapters import to_aiogram_markup

logger = logging.getLogger(__name__)

Addr = tuple  # (platform: "tg" | "max", id: int)
TG, MAX = "tg", "max"


def tg_addr(tg_id: int) -> Addr:
    return (TG, int(tg_id))


def max_addr(max_id: int) -> Addr:
    return (MAX, int(max_id))


def fmt_addr(addr: Addr) -> str:
    platform, ident = addr
    return f"m{ident}" if platform == MAX else str(ident)


def parse_addr(raw: str) -> Optional[Addr]:
    raw = (raw or "").strip()
    if raw.startswith("m") and raw[1:].isdigit():
        return (MAX, int(raw[1:]))
    if raw.lstrip("-").isdigit():
        return (TG, int(raw))
    return None


def addrs_of(student, client=None) -> list:
    """Все адреса родителей ученика: клиент (tg, max), затем parent_tg_ids, parent_max_ids."""
    out: list = []

    def _add(a: Addr) -> None:
        if a not in out:
            out.append(a)

    if client is not None:
        if getattr(client, "tg_id", None):
            _add(tg_addr(client.tg_id))
        if getattr(client, "max_id", None):
            _add(max_addr(client.max_id))
    for i in getattr(student, "parent_tg_ids", None) or []:
        _add(tg_addr(i))
    for i in getattr(student, "parent_max_ids", None) or []:
        _add(max_addr(i))
    return out


class ParentNotifier:
    default: "ParentNotifier | None" = None  # экземпляр процесса (ставится в __main__)

    def __init__(self, tg_bot=None, max_bot=None) -> None:
        self.tg_bot = tg_bot
        self.max_bot = max_bot

    def set_as_default(self) -> "ParentNotifier":
        ParentNotifier.default = self
        return self

    async def send(self, addr: Optional[Addr], text: str, rows=None) -> bool:
        """Доставить текст (HTML) с кнопками по адресу. False — не доставлено."""
        if not addr:
            return False
        platform, ident = addr
        try:
            if platform == TG:
                if self.tg_bot is None:
                    return False
                await self.tg_bot.send_message(ident, text, reply_markup=to_aiogram_markup(rows))
                return True
            if platform == MAX:
                if self.max_bot is None:
                    logger.warning("MAX-бот не запущен: сообщение для max_id=%s не доставлено", ident)
                    return False
                from bot.max.render import send_screen
                await send_screen(self.max_bot, ident, text, rows)
                return True
        except TelegramAPIError as exc:
            logger.warning("Родителю %s не доставлено: %s", fmt_addr(addr), exc)
        except Exception as exc:  # ошибки MAX API
            logger.warning("Родителю %s не доставлено: %s", fmt_addr(addr), exc)
        return False

    async def send_many(self, addrs: Iterable[Addr], text: str, rows=None) -> int:
        sent = 0
        seen: set = set()
        for addr in addrs:
            if not addr or addr in seen:
                continue
            seen.add(addr)
            if await self.send(addr, text, rows):
                sent += 1
        return sent

    async def send_to_student(self, student, client, text: str, rows=None) -> tuple[int, int]:
        """(получателей, доставлено) — всем родителям ученика на обеих платформах."""
        addrs = addrs_of(student, client)
        return len(addrs), await self.send_many(addrs, text, rows)


def resolve_notifier(tg_bot=None) -> ParentNotifier:
    """Общий notifier процесса; если не настроен (тесты, скрипты) — только Telegram-бот вызова."""
    if ParentNotifier.default is not None:
        return ParentNotifier.default
    return ParentNotifier(tg_bot=tg_bot)
