"""Кнопка меню «Кабинет» (Mini App) в чатах сотрудников бота.

    .venv/bin/python scripts/set_miniapp_menu.py            # админам и педагогам из листа users
    .venv/bin/python scripts/set_miniapp_menu.py --admins   # только админам
    .venv/bin/python scripts/set_miniapp_menu.py --reset    # вернуть стандартное меню

Ставится через Bot API setChatMenuButton для каждого чата отдельно — у родителей меню
не меняется. URL берётся из MINIAPP_URL, роль внутри кабинета определяет сервер.
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


async def main(reset: bool, admins_only: bool) -> None:
    if not reset and not settings.miniapp_url:
        print("MINIAPP_URL пуст — нечего ставить", file=sys.stderr)
        sys.exit(2)
    users = await UserRepository(SheetsClient(settings), settings.sheet_users).get_all()
    staff = [u for u in users if u.is_admin or (not admins_only and u.teacher_id)]
    bot = Bot(settings.bot_token)
    try:
        for user in staff:
            role = "админ" if user.is_admin else "педагог"
            button = MenuButtonDefault() if reset else MenuButtonWebApp(text="Кабинет", web_app=WebAppInfo(url=settings.miniapp_url))
            try:
                await bot.set_chat_menu_button(chat_id=user.tg_id, menu_button=button)
                print(f"{user.tg_id} ({role}): {'сброшено' if reset else 'кнопка «Кабинет» → ' + settings.miniapp_url}")
            except Exception as exc:  # чат ещё не начат с ботом и т. п.
                print(f"{user.tg_id} ({role}): ошибка — {exc}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--admins", action="store_true", help="только админам")
    args = parser.parse_args()
    asyncio.run(main(args.reset, args.admins))
