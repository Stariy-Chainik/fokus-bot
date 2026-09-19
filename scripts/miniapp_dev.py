"""Локальный сервер Mini App без Telegram-поллинга: API + фронт на одном порту.

    MINIAPP_DEV_TG_ID=<ваш tg_id> .venv/bin/python scripts/miniapp_dev.py [--port 8090]

Открыть http://localhost:8090/app/?dev=1 — заголовок `Authorization: dev` подставит
tg_id из MINIAPP_DEV_TG_ID (роль берётся из листа users). Данные — живая таблица из .env,
поэтому кнопки подтверждения оплат пишут в неё по-настоящему.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

from aiohttp import web  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot.__main__ import _build_dispatcher  # noqa: E402
from bot.api import (  # noqa: E402
    register_admin_api, register_miniapp_api, register_miniapp_static, register_teacher_api,
)
from bot.services import rate_history  # noqa: E402
from config.settings import settings  # noqa: E402


async def main(port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if not settings.miniapp_dev_tg_id:
        print("Задайте MINIAPP_DEV_TG_ID (tg_id администратора из листа users)", file=sys.stderr)
        sys.exit(2)
    dp = _build_dispatcher(MemoryStorage())
    rate_history.load(await dp["rate_history_repo"].get_all())
    app = web.Application()
    register_miniapp_api(app, dp)
    register_admin_api(app, dp)
    register_teacher_api(app, dp)
    register_miniapp_static(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Mini App: http://localhost:{port}/app/?dev=1  (tg_id={settings.miniapp_dev_tg_id})")
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    asyncio.run(main(parser.parse_args().port))
