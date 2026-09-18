"""Кнопка меню «Кабинет» (Mini App) в чатах администраторов бота.

    .venv/bin/python scripts/set_miniapp_menu.py            # поставить всем админам из листа users
    .venv/bin/python scripts/set_miniapp_menu.py --reset     # вернуть стандартное меню

Ставится через Bot API setChatMenuButton для каждого admin-чата отдельно — у родителей и
педагогов меню не меняется. URL берётся из MINIAPP_URL.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

from aiogram import Bot  # noqa: E402
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo  # noqa: E402

from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from bot.repositories.user_repo import UserRepository  # noqa: E402
from config.settings import settings  # noqa: E402


async def main(reset: bool) -> None:
    if not reset and not settings.miniapp_url:
        print("MINIAPP_URL пуст — нечего ставить", file=sys.stderr)
        sys.exit(2)
    admins = await UserRepository(SheetsClient(settings), settings.sheet_users).get_admins()
    bot = Bot(settings.bot_token)
    try:
        for admin in admins:
            button = MenuButtonDefault() if reset else MenuButtonWebApp(text="Кабинет", web_app=WebAppInfo(url=settings.miniapp_url))
            try:
                await bot.set_chat_menu_button(chat_id=admin.tg_id, menu_button=button)
                print(f"{admin.tg_id}: {'сброшено' if reset else 'кнопка «Кабинет» → ' + settings.miniapp_url}")
            except Exception as exc:  # чат ещё не начат с ботом и т. п.
                print(f"{admin.tg_id}: ошибка — {exc}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    asyncio.run(main(parser.parse_args().reset))
