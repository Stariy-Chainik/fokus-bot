"""Приглашение родителей в группу (решение владельца 15.10.2026): короткий текст со ссылками на бота.

Раньше педагоги брали готовые сообщения из Google-документа; теперь кнопка «🔗 Пригласить родителей» в карточке
группы (кабинеты педагога и администратора) отдаёт текст со ссылками этой группы — Telegram и, если включён, MAX
(`t.me/<бот>?start=g_…`, тот же payload, что печатает `scripts/gen_group_links.py`). Отправить в чат родителей —
«✈️ Отправить в Telegram» (окно «Поделиться») или «📋 Скопировать».
"""
from __future__ import annotations

import logging
import re

from bot.utils.group_links import build_start_payload
from config.settings import settings

logger = logging.getLogger(__name__)

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️‍⬀-⯿]")
_tg_username = ""


def plain_name(name: str) -> str:
    """Название группы без эмодзи — в сообщении родителям."""
    return " ".join(_EMOJI.sub("", name or "").split())


async def tg_username(bot) -> str:
    global _tg_username
    if not _tg_username and bot is not None:
        try:
            _tg_username = (await bot.me()).username or ""
        except Exception as exc:                        # без имени бота — без ссылки Telegram
            logger.warning("Приглашение в группу: имя бота Telegram не получено: %s", exc)
    return _tg_username


def max_username() -> str:
    if not settings.max_bot_token:
        return ""
    try:
        from bot.max.render import ME                   # заполняет run_max при запуске бота MAX
        return ME.get("username") or ""
    except Exception:
        return ""


def invite_text(group_name: str, tg: str, mx: str) -> tuple[str, str]:
    """(полный текст, текст без строки Telegram — для «Поделиться», где ссылка идёт первой строкой)."""
    head = f"Родители группы «{plain_name(group_name)}»! Подключитесь к кабинету клуба «Фокус»: счета, оплата и расписание ребёнка."
    tail = "Откройте ссылку, запустите бота и выберите ребёнка из списка. Ссылка только для родителей нашей группы."
    links = ([f"Telegram: {tg}"] if tg else []) + ([f"MAX: {mx}"] if mx else [])
    full = "\n\n".join([head, "\n".join(links), tail]) if links else head
    rest = "\n\n".join([head] + ([f"MAX: {mx}"] if mx else []) + [tail])
    return full, rest


async def group_invite(group_id: str, group_name: str, bot) -> dict:
    payload = build_start_payload(group_id, settings.group_link_secret or settings.bot_token)
    tg_user, max_user = await tg_username(bot), max_username()
    tg = f"https://t.me/{tg_user}?start={payload}" if tg_user else ""
    mx = f"https://max.ru/{max_user}?start={payload}" if max_user else ""
    text, share_text = invite_text(group_name, tg, mx)
    return {"groupId": group_id, "groupName": plain_name(group_name), "tg": tg, "max": mx,
            "text": text, "shareText": share_text}
