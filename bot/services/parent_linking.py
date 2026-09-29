"""Второй родитель к ребёнку — только с одобрения администратора (решение владельца 29.09.2026).

Первый родитель привязывается сам (ссылка группы или фамилия). Если у ребёнка уже есть привязанный
родитель, вместо привязки уходит заявка: админам в Telegram с кнопками «Одобрить / Отклонить»
(`admin_child_ok:` / `admin_child_no:`) и строка в очереди решений кабинета (KIND_CHILD).
Одобрение привязывает через `student_repo.add_parent(addr)` — одинаково для Telegram и MAX.
"""
from __future__ import annotations

import logging

from bot.services.parent_notifier import fmt_addr
from bot.services.pending_queue import KIND_CHILD, queue_action
from bot.utils.notify import notify

logger = logging.getLogger(__name__)


def needs_approval(student, addr) -> bool:
    """True — у ребёнка уже есть другой родитель, значит этого добавляет администратор."""
    others = [a for a in student.parent_addrs if a != addr]
    return bool(others)


async def request_approval(tg_bot, user_repo, pending_repo, student, addr, sender_name: str, source: str) -> None:
    from bot.keyboards.client import kb_admin_approve_child      # локально: клавиатуры тянут aiogram-слой
    platform = "MAX" if addr[0] == "max" else "Telegram"
    text = (f"👥 <b>Второй родитель просит доступ</b>\n\n"
            f"Кто: {sender_name} ({platform} <code>{addr[1]}</code>)\n"
            f"Ученик: <b>{student.name}</b>\n"
            f"Откуда: {source}\n\n"
            f"У ребёнка уже есть привязанный родитель — одобрите, если это член семьи.")
    admins = [u.tg_id for u in await user_repo.get_admins()]
    await notify(tg_bot, admins, text, reply_markup=kb_admin_approve_child(fmt_addr(addr), student.student_id))
    await queue_action(pending_repo, KIND_CHILD, student, parent_addr=fmt_addr(addr),
                       comment=f"второй родитель · {sender_name} · {source}")
    logger.info("Второй родитель %s → %s: заявка администратору (%s)", fmt_addr(addr), student.student_id, source)


WAIT_TEXT = ("⏳ К ребёнку <b>{name}</b> уже привязан другой родитель.\n\n"
             "Мы отправили заявку администратору — после одобрения вам придёт сообщение, и счета станут доступны.")
