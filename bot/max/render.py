"""Адаптеры экранов (текст + ряды кнопок) под MAX Bot API."""
from __future__ import annotations
import logging

from maxapi.types import CallbackButton, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

MAX_TEXT = 3900


def to_max_markup(rows):
    if not rows:
        return None
    b = InlineKeyboardBuilder()
    for row in rows:
        buttons = [
            LinkButton(text=btn.label, url=btn.value) if btn.kind in ("url", "app")   # Mini App в MAX — обычная ссылка
            else CallbackButton(text=btn.label, payload=btn.value)
            for btn in row
        ]
        b.row(*buttons)
    return b.as_markup()


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Режет длинный текст по строкам (MAX: ≤ 4000 символов на сообщение)."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


async def send_screen(bot, user_id: int, text: str, rows=None, media=None) -> None:
    """Новое сообщение пользователю: текст (HTML), кнопки, при необходимости вложение."""
    chunks = split_text(text)
    kb = to_max_markup(rows)
    for i, chunk in enumerate(chunks):
        atts = []
        if i == len(chunks) - 1:
            if media is not None:
                atts.append(media)
            if kb is not None:
                atts.append(kb)
        await bot.send_message(user_id=user_id, text=chunk, attachments=atts or None)


async def edit_screen(event, text: str, rows=None) -> None:
    """Правка сообщения с кнопкой (и ответ на callback). Если не вышло — новое сообщение."""
    kb = to_max_markup(rows)
    if len(text) <= MAX_TEXT and event.message is not None:
        try:
            await event.edit(text=text, attachments=[kb] if kb else [])
            return
        except Exception as exc:  # сообщение удалено / устарело
            logger.info("MAX edit не удался (%s) — отправляем новое", exc)
    try:
        await event.ack()
    except Exception:
        pass
    await send_screen(event.bot, event.callback.user.user_id, text, rows)


async def alert(event, text: str) -> None:
    """Короткий ответ на callback без изменения экрана."""
    try:
        await event.ack(notification=text)
    except Exception as exc:
        logger.info("MAX ack не удался: %s", exc)
