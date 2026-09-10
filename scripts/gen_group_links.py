"""Генерирует ссылки-приглашения для всех групп (раздать педагогам в чаты).

Запуск:
    .venv/bin/python scripts/gen_group_links.py

Ссылка ведёт на бота из BOT_TOKEN текущего .env: локально с тестовым
токеном получатся ссылки на тестового бота — удобно для проверки.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot  # noqa: E402

from config.settings import settings  # noqa: E402
from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from bot.repositories.group_repo import GroupRepository  # noqa: E402
from bot.repositories.branch_repo import BranchRepository  # noqa: E402
from bot.repositories.student_group_repo import StudentGroupRepository  # noqa: E402
from bot.utils.group_links import build_start_payload  # noqa: E402


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    me = await bot.get_me()
    await bot.session.close()

    max_username = None
    if settings.max_bot_token:
        try:
            from maxapi import Bot as MaxBot
            mbot = MaxBot(settings.max_bot_token)
            max_username = getattr(await mbot.get_me(), "username", None)
        except Exception as exc:  # библиотека не установлена / токен невалиден
            print(f"MAX: ссылки не построены ({exc})")

    sc = SheetsClient(settings)
    groups = await GroupRepository(sc, settings.sheet_groups).get_all()
    branches = {b.branch_id: b.name for b in await BranchRepository(sc, settings.sheet_branches).get_all()}
    sg_repo = StudentGroupRepository(sc, settings.sheet_student_groups)
    counts = {}
    for sg in await sg_repo.get_all():
        counts[sg.group_id] = counts.get(sg.group_id, 0) + 1

    secret = settings.group_link_secret or settings.bot_token
    print(f"Бот: @{me.username}\n")
    print(f"{'Группа':40} {'Филиал':18} {'Учеников':>8}  Ссылка")
    for g in sorted(groups, key=lambda g: (branches.get(g.branch_id, ""), g.name)):
        payload = build_start_payload(g.group_id, secret)
        link = f"https://t.me/{me.username}?start={payload}"
        if max_username:
            link += f"\n    MAX: https://max.ru/{max_username}?start={payload}"
        print(f"{g.name:40} {branches.get(g.branch_id, '?'):18} {counts.get(g.group_id, 0):>8}  {link}")


if __name__ == "__main__":
    asyncio.run(main())
