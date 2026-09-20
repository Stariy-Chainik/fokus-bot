"""Кнопка меню «Кабинет» (Mini App) в чатах пользователей бота.

    .venv/bin/python scripts/set_miniapp_menu.py            # всем: админам, педагогам, родителям, спортсменам
    .venv/bin/python scripts/set_miniapp_menu.py --admins   # только админам
    .venv/bin/python scripts/set_miniapp_menu.py --staff    # админам и педагогам
    .venv/bin/python scripts/set_miniapp_menu.py --reset    # вернуть стандартное меню (тем же адресатам)

Ставится через Bot API setChatMenuButton для каждого чата отдельно. URL один и тот же
(MINIAPP_URL) — какой кабинет откроется, решает сервер по роли: админ → педагог →
родитель → спортсмен. Чат, который не начат с ботом, пропускается с сообщением.
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
from bot.repositories.student_repo import StudentRepository  # noqa: E402
from bot.repositories.user_repo import UserRepository  # noqa: E402
from config.settings import settings  # noqa: E402


async def _targets(scope: str) -> dict[int, str]:
    """tg_id → роль для подписи в выводе; один человек попадает в список один раз."""
    sheets = SheetsClient(settings)
    out: dict[int, str] = {}
    for user in await UserRepository(sheets, settings.sheet_users).get_all():
        if user.is_admin:
            out[user.tg_id] = "админ"
        elif user.teacher_id and scope in ("staff", "all"):
            out.setdefault(user.tg_id, "педагог")
    if scope == "all":
        for student in await StudentRepository(sheets, settings.sheet_students).get_all():
            for kind, ident in student.parent_addrs:          # MAX-родителям кнопка не нужна
                if kind == "tg":
                    out.setdefault(int(ident), "родитель")
            if student.athlete_tg_id:
                out.setdefault(int(student.athlete_tg_id), "спортсмен")
    return out


async def main(reset: bool, scope: str) -> None:
    if not reset and not settings.miniapp_url:
        print("MINIAPP_URL пуст — нечего ставить", file=sys.stderr)
        sys.exit(2)
    targets = await _targets(scope)
    button = MenuButtonDefault() if reset else MenuButtonWebApp(
        text="Кабинет", web_app=WebAppInfo(url=settings.miniapp_url))
    bot = Bot(settings.bot_token)
    ok = failed = 0
    try:
        for tg_id, role in targets.items():
            try:
                await bot.set_chat_menu_button(chat_id=tg_id, menu_button=button)
                ok += 1
                print(f"{tg_id} ({role}): {'сброшено' if reset else 'кнопка «Кабинет»'}")
            except Exception as exc:                          # чат ещё не начат с ботом и т. п.
                failed += 1
                print(f"{tg_id} ({role}): ошибка — {exc}")
    finally:
        await bot.session.close()
    print(f"\nИтого: {ok} успешно, {failed} с ошибкой из {len(targets)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--admins", action="store_true", help="только админам")
    parser.add_argument("--staff", action="store_true", help="админам и педагогам")
    args = parser.parse_args()
    asyncio.run(main(args.reset, "admins" if args.admins else "staff" if args.staff else "all"))
