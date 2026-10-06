"""Кто привязан к ребёнку и заявка «отвязать другого родителя» (решение владельца 06.10.2026).

Родитель в кабинете видит всех, кто привязан к его ребёнку (имя и @ник из Telegram, мессенджер,
когда привязан), и может попросить отвязать лишнего. Сам он никого не отвязывает: заявка ложится
в `pending_actions` (KIND_UNLINK: `parent_addr` — кто просит, `teacher_keys` — кого отвязать) и
уходит администраторам в Telegram с кнопками `unlink_ok:` / `unlink_no:`; то же решение есть в
кабинете администратора («Ждут решения»). Одобрение снимает адрес у ученика (и у карточки клиента,
если по ней шли уведомления), обоим родителям — сообщение. Себя родитель по-прежнему отвязывает
сам — «это не мой ребёнок» (DELETE /api/parent/children/{sid}).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time

from bot.services import activity
from bot.services.parent_notifier import MAX, fmt_addr, parse_addr
from bot.services.pending_queue import DONE, REJECTED, queue_action
from bot.utils.notify import notify

logger = logging.getLogger(__name__)

KIND_UNLINK = "unlink"

_NAME_TTL, _MISS_TTL = 6 * 3600, 3600        # имя из Telegram держим 6 ч, «не нашли» — час
_names: dict[int, tuple[float, str, str]] = {}


def parent_key(student_id: str, addr, secret: str) -> str:
    """Ключ строки «родитель ребёнка» для кабинета: tg_id других родителей наружу не отдаём."""
    return hmac.new(secret.encode(), f"{student_id}:{fmt_addr(addr)}".encode(), hashlib.sha256).hexdigest()[:16]


def platform_label(addr) -> str:
    return "MAX" if addr[0] == MAX else "Telegram"


async def _tg_name(tg_bot, tg_id: int) -> tuple[str, str]:
    """(имя, username) из Telegram по getChat, с кешем. Пусто — бот не знает этого пользователя."""
    hit = _names.get(tg_id)
    now = time.monotonic()
    if hit and now - hit[0] < (_NAME_TTL if hit[1] else _MISS_TTL):
        return hit[1], hit[2]
    name = username = ""
    get_chat = getattr(tg_bot, "get_chat", None)
    if get_chat is not None:
        try:
            chat = await get_chat(tg_id)
            name = " ".join(x for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if x)
            username = getattr(chat, "username", None) or ""
        except Exception as exc:                  # заблокировал бота, удалил аккаунт — покажем без имени
            logger.info("Имя родителя %s из Telegram не получено: %s", tg_id, exc)
    _names[tg_id] = (now, name, username)
    return name, username


async def parent_infos(tg_bot, addrs: list, client_names: dict) -> dict:
    """addr → {"name", "username"}: карточка клиента школы, иначе имя из Telegram (в MAX имени нет)."""
    async def one(addr):
        if addr in client_names:
            return addr, {"name": client_names[addr], "username": ""}
        if addr[0] != MAX and tg_bot is not None:
            name, username = await _tg_name(tg_bot, addr[1])
            return addr, {"name": name, "username": username}
        return addr, {"name": "", "username": ""}
    return dict(await asyncio.gather(*(one(a) for a in addrs)))


def describe(addr, info: dict | None) -> str:
    """«Иванова Мария (@ivanova, Telegram)» / «Родитель в MAX»."""
    info = info or {}
    tail = ", ".join(x for x in (f"@{info['username']}" if info.get("username") else "", platform_label(addr)) if x)
    return f"{info['name']} ({tail})" if info.get("name") else f"Родитель в {platform_label(addr)}" + (
        f" (@{info['username']})" if info.get("username") else "")


async def linked_since(activity_repo, student_id: str) -> dict:
    """addr → дата последней привязки к ученику по ленте событий (пусто — привязан до ленты)."""
    if activity_repo is None:
        return {}
    prefix = f"Родитель привязан: {student_id} · "
    out: dict = {}
    try:
        events = await activity_repo.get_all()
    except Exception as exc:                      # лента вспомогательная
        logger.warning("Кто привязан: лента событий недоступна: %s", exc)
        return {}
    for e in sorted(events, key=lambda x: x.ts):
        if e.text.startswith(prefix):
            platform, _, ident = e.text[len(prefix):].partition(" ")
            if ident.isdigit():
                out[(MAX if platform == "MAX" else "tg", int(ident))] = e.ts[:10]
    return out


def target_of(action):
    """Кого отвязать: адрес хранится в teacher_keys (кол. 16), кто просит — в parent_addr."""
    return parse_addr(action.keys[0]) if action.keys else None


async def open_requests(pending_repo, student_id: str) -> list:
    if pending_repo is None:
        return []
    return [a for a in await pending_repo.get_open() if a.kind == KIND_UNLINK and a.student_id == student_id]


async def request_unlink(
    pending_repo, tg_bot, user_repo, student, requester, target, requester_info: dict, target_info: dict,
    reason: str, since: str = "",
):
    """Поставить заявку и разослать администраторам. (action, created): created=False — такая уже ждёт."""
    from bot.keyboards.client import kb_unlink_decide          # локально: клавиатуры тянут aiogram-слой

    existing = next((a for a in await open_requests(pending_repo, student.student_id)
                     if target_of(a) == target), None)
    if existing is not None:
        return existing, False
    who, whom = describe(requester, requester_info), describe(target, target_info)
    action = await queue_action(
        pending_repo, KIND_UNLINK, student, parent_addr=fmt_addr(requester),
        comment=f"отвязать: {whom} · просит: {who}" + (f" · причина: {reason}" if reason else ""),
        teacher_keys=[fmt_addr(target)],
    )
    if action is None:
        return None, False
    text = (f"👥 <b>Родитель просит отвязать другого родителя</b>\n\n"
            f"Ученик: <b>{student.name}</b>\n"
            f"Отвязать: {whom}, <code>{target[1]}</code>{f', привязан {since}' if since else ''}\n"
            f"Просит: {who}, <code>{requester[1]}</code>\n"
            + (f"Причина: {reason}\n" if reason else "")
            + "\nОтвязать — этот человек перестанет видеть счета и занятия ребёнка и получать уведомления.")
    admins = [u.tg_id for u in await user_repo.get_admins()]
    await notify(tg_bot, admins, text, reply_markup=kb_unlink_decide(action.action_id))
    logger.info("Отвязка родителя: %s просит отвязать %s от %s → %s", fmt_addr(requester), fmt_addr(target),
                student.student_id, action.action_id)
    return action, True


async def _drop_client_addr(student_repo, client_repo, student, target) -> None:
    """Уведомления идут и на адрес карточки клиента (parent_notifier.addrs_of): снимаем и его,
    если этот родитель не остался привязан к другим детям той же семьи."""
    if client_repo is None or not student.client_id:
        return
    client = await client_repo.get_by_id(student.client_id)
    if client is None:
        return
    is_client = (client.max_id == target[1]) if target[0] == MAX else (client.tg_id == target[1])
    if not is_client:
        return
    family = [s for s in await student_repo.get_all()
              if s.client_id == client.client_id and s.student_id != student.student_id]
    if any(target in s.parent_addrs for s in family):
        return
    if target[0] == MAX:
        await client_repo.set_max_id(client.client_id, None)
    else:
        await client_repo.clear_tg_id(client.client_id)
    logger.info("Отвязка родителя: адрес %s снят и с карточки клиента %s", fmt_addr(target), client.client_id)


async def approve_unlink(pending_repo, student_repo, client_repo, notifier, action, decided_by: int):
    """Отвязать по заявке. None — заявку уже решили; иначе ученик."""
    target = target_of(action)
    if target is None or not await pending_repo.claim(action.action_id, DONE, decided_by):
        return None
    student = await student_repo.get_by_id(action.student_id)
    if student is None:
        return None
    await student_repo.remove_parent(student.student_id, target)
    await _drop_client_addr(student_repo, client_repo, student, target)
    await activity.record(activity.QUEUE, f"Заявка {action.action_id} одобрена: родитель отвязан от "
                          f"{student.student_id} по просьбе другого родителя", actor=decided_by, ref=action.action_id)
    requester = parse_addr(action.parent_addr) if action.parent_addr else None
    if notifier is not None:
        for addr, text in (
            (requester, f"✅ Администратор одобрил заявку: лишний родитель отвязан от ученика <b>{student.name}</b>."),
            (target, f"Администратор школы закрыл вам доступ к ученику <b>{student.name}</b>: счета, занятия и "
                     f"уведомления по нему больше не приходят. Если это ошибка — напишите администратору школы."),
        ):
            if addr is None:
                continue
            try:
                await notifier.send(addr, text)
            except Exception as exc:
                logger.warning("Отвязка родителя: %s не ушло: %s", fmt_addr(addr), exc)
    logger.info("Отвязка родителя: %s отвязан от %s по заявке %s (решил %s)", fmt_addr(target),
                student.student_id, action.action_id, decided_by)
    return student


async def reject_unlink(pending_repo, notifier, action, decided_by: int) -> bool:
    if not await pending_repo.claim(action.action_id, REJECTED, decided_by):
        return False
    await activity.record(activity.QUEUE, f"Заявка {action.action_id} отклонена: привязка к "
                          f"{action.student_id} оставлена", actor=decided_by, ref=action.action_id)
    requester = parse_addr(action.parent_addr) if action.parent_addr else None
    if notifier is not None and requester is not None:
        try:
            await notifier.send(requester, f"❌ Администратор оставил привязку к ученику <b>{action.student_name}</b> "
                                           "без изменений. Вопросы — администратору школы.")
        except Exception as exc:
            logger.warning("Отвязка родителя: %s не ушло: %s", action.parent_addr, exc)
    return True
