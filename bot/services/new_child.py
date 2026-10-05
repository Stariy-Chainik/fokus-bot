"""Заявка «моего ребёнка нет в группе» → карточку заводит администратор или педагог.

Решение владельца 05.10.2026: родитель сам ученика не создаёт. На экране группы по ссылке он жмёт
«❌ Моего ребёнка нет в списке», вводит фамилию и имя, и в `pending_actions` появляется строка
KIND_NEWCHILD (имя ребёнка, адрес родителя, группа — в `teacher_keys`). Администраторам и педагогам
группы уходит сообщение с кнопками `nchild_ok:` / `nchild_no:`; то же решение доступно в кабинете
администратора («Ждут решения»). Одобрение: карточка ученика в группе + привязка родителя + ему
сообщение с меню. Без транспорта — хендлеры Telegram/MAX и API зовут одни и те же функции.
"""
from __future__ import annotations

import logging

from bot.services import activity
from bot.services.parent_notifier import fmt_addr, parse_addr
from bot.services.pending_queue import DONE, REJECTED, queue_action
from bot.utils.notify import notify

logger = logging.getLogger(__name__)

KIND_NEWCHILD = "newchild"
MIN_NAME_LEN = 3


def normalize_name(raw: str) -> str | None:
    """«Фамилия Имя» одной строкой; None — слишком коротко или мусор."""
    name = " ".join(raw.split())
    if len(name) < MIN_NAME_LEN or not any(ch.isalpha() for ch in name):
        return None
    return name


def group_of(action) -> str:
    """Группа заявки хранится в teacher_keys (кол. 16): у новой карточки student_id ещё нет."""
    return action.keys[0] if action.keys else ""


async def request_new_child(
    pending_repo, tg_bot, user_repo, teacher_repo, teacher_group_repo, group, name: str, addr,
    sender_name: str,
):
    """Поставить заявку в очередь и разослать её администраторам и педагогам группы."""
    from bot.keyboards.client import kb_new_child_decide      # локально: клавиатуры тянут aiogram-слой

    action = await queue_action(
        pending_repo, KIND_NEWCHILD, student_name=name, parent_addr=fmt_addr(addr),
        comment=f"{group.name} · {sender_name}", teacher_keys=[group.group_id],
    )
    if action is None:
        return None
    platform = "MAX" if addr[0] == "max" else "Telegram"
    text = (f"🆕 <b>Родитель не нашёл ребёнка в группе</b>\n\n"
            f"Ребёнок: <b>{name}</b>\nГруппа: {group.name}\n"
            f"Родитель: {sender_name} ({platform} <code>{addr[1]}</code>)\n\n"
            f"Одобрить — завести карточку в группе и привязать родителя.")
    teachers = {t.teacher_id: t for t in await teacher_repo.get_all()}
    teacher_tgs = {
        teachers[tg.teacher_id].tg_id for tg in await teacher_group_repo.get_all()
        if tg.group_id == group.group_id and tg.teacher_id in teachers and teachers[tg.teacher_id].tg_id
    }
    admins = {u.tg_id for u in await user_repo.get_admins()}
    await notify(tg_bot, sorted(admins | teacher_tgs), text, reply_markup=kb_new_child_decide(action.action_id))
    logger.info("Новый ребёнок по ссылке: %s в %s от %s → заявка %s", name, group.group_id,
                fmt_addr(addr), action.action_id)
    return action


async def may_decide(user, action, teacher_group_repo) -> bool:
    """Администратор — любую, педагог — заявки своих групп."""
    if user is None:
        return False
    if user.is_admin:
        return True
    if not user.teacher_id:
        return False
    gid = group_of(action)
    return any(tg.teacher_id == user.teacher_id and tg.group_id == gid
               for tg in await teacher_group_repo.get_all())


async def approve_new_child(pending_repo, student_repo, student_group_repo, notifier, action, decided_by: int):
    """Завести карточку, добавить в группу, привязать родителя. None — заявку уже решили."""
    if not await pending_repo.claim(action.action_id, DONE, decided_by):
        return None
    student = await student_repo.add(name=action.student_name)
    gid = group_of(action)
    if gid:
        await student_group_repo.add(student.student_id, gid)
    addr = parse_addr(action.parent_addr) if action.parent_addr else None
    if addr is not None:
        await student_repo.add_parent(student.student_id, addr)
    await activity.record(activity.STUDENT, f"Новый ученик по заявке родителя: {student.student_id} · {gid}",
                          actor=decided_by, ref=student.student_id)
    if notifier is not None and addr is not None:
        from config.settings import settings
        from bot.screens.parent_menu import menu_rows
        try:
            await notifier.send(
                addr, f"✅ Ребёнок <b>{student.name}</b> добавлен в группу, вы привязаны к нему.\n\nВыберите раздел:",
                rows=menu_rows(platform=addr[0], receipt_email=settings.parent_receipt_email),
            )
        except Exception as exc:
            logger.warning("Новый ребёнок: родителю %s не ушло: %s", action.parent_addr, exc)
    logger.info("Новый ребёнок %s заведён по заявке %s (решил %s)", student.student_id, action.action_id, decided_by)
    return student


async def reject_new_child(pending_repo, notifier, action, decided_by: int) -> bool:
    if not await pending_repo.claim(action.action_id, REJECTED, decided_by):
        return False
    addr = parse_addr(action.parent_addr) if action.parent_addr else None
    if notifier is not None and addr is not None:
        try:
            await notifier.send(addr, f"❌ Заявку на ребёнка <b>{action.student_name}</b> отклонили. "
                                      "Свяжитесь с педагогом или администратором.")
        except Exception as exc:
            logger.warning("Новый ребёнок: родителю %s не ушло: %s", action.parent_addr, exc)
    return True
