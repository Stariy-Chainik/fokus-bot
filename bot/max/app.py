"""Сборка и запуск MAX-бота (polling) рядом с Telegram-ботом в одном процессе."""
from __future__ import annotations
import asyncio
import logging

from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import ParseMode

from .middlewares import DepsMiddleware, DedupMiddleware
from .handlers import router as parent_router

logger = logging.getLogger(__name__)


def build(token: str, deps: dict, tg_bot):
    bot = Bot(token, format=ParseMode.HTML)
    dp = Dispatcher(router_id="max")
    dp.register_outer_middleware(DedupMiddleware())
    dp.register_outer_middleware(DepsMiddleware({**deps, "tg_bot": tg_bot, "max_bot": bot}))
    dp.include_routers(parent_router)
    return bot, dp


async def run_max(bot, dp, *, retry_delay: int = 15) -> None:
    """Polling с перезапуском: сбой MAX не должен ронять Telegram-бота."""
    while True:
        try:
            me = await bot.get_me()
            logger.info("MAX: запуск polling для @%s", getattr(me, "username", "?"))
            await dp.start_polling(bot, skip_updates=True)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("MAX polling упал: %s — перезапуск через %d с", exc, retry_delay)
            await asyncio.sleep(retry_delay)
