"""Рассылка коротких уведомлений (спортсмену, родителям, админам)."""
from __future__ import annotations
import logging
from typing import Awaitable, Iterable

from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


async def notify(bot, tg_ids: Iterable[int | None], text: str, reply_markup=None) -> int:
    """Отправляет text каждому tg_id (без дублей, None пропускается).
    Ошибки доставки (бот не запущен, заблокирован) логируются и не прерывают вызов.
    Возвращает число доставленных."""
    sent = 0
    seen: set[int] = set()
    for tg_id in tg_ids:
        if not tg_id or tg_id in seen:
            continue
        seen.add(tg_id)
        try:
            await bot.send_message(tg_id, text, reply_markup=reply_markup)
            sent += 1
        except TelegramAPIError as exc:
            logger.warning("Уведомление tg_id=%s не доставлено: %s", tg_id, exc)
    return sent


async def notify_safely(send: Awaitable, failure_log: str) -> bool:
    """Доставка best-effort: `await send`; любая ошибка логируется как error и не прерывает хендлер.

    failure_log — формат сообщения лога с одним %s под исключение
    (тексты сохранены по месту вызова).
    """
    try:
        await send
        return True
    except Exception as exc:  # уведомление не должно ронять основной сценарий
        logger.error(failure_log, exc)
        return False
