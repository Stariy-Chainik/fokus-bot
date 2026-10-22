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
from dataclasses import dataclass

from bot.services import activity
from bot.services.parent_notifier import fmt_addr, parse_addr
from bot.services.pending_queue import DONE, REJECTED, queue_action
from bot.utils.notify import notify

logger = logging.getLogger(__name__)

KIND_NEWCHILD = "newchild"
MIN_NAME_LEN = 3


def _norm(name: str) -> str:
    return " ".join(str(name or "").lower().replace("ё", "е").split())


@dataclass
class NewChildRequest:
    """Результат `request_new_child`: заявка и была ли она уже открыта (повтор — без новых сообщений)."""
    action: object
    duplicate: bool = False


@dataclass
class ApprovedChild:
    """Результат `approve_new_child`: ученик и был ли он уже в базе (родитель привязан к существующей карточке)."""
    student: object
    existing: bool = False
    added_to_group: bool = False


async def open_duplicate(pending_repo, name: str, group_id: str):
    """Открытая заявка «нет в списке» с тем же именем в ту же группу — от любого родителя и из любого
    мессенджера (21.10.2026: заявка из MAX и из Telegram давала две карточки и двойной «пришёл»)."""
    if pending_repo is None:
        return None
    try:
        return next((a for a in await pending_repo.get_open()
                     if a.kind == KIND_NEWCHILD and _norm(a.student_name) == _norm(name) and group_of(a) == group_id), None)
    except Exception as exc:                       # очередь вспомогательная — заявку всё равно отправим
        logger.warning("Очередь решений недоступна: %s", exc)
        return None


async def existing_match(student_repo, student_group_repo, name: str, group_id: str):
    """Ученик с таким же именем уже в базе: в этой группе — он; иначе единственный однофамилец-тёзка;
    несколько — не угадываем (None → новая карточка)."""
    same = [s for s in await student_repo.get_all() if _norm(s.name) == _norm(name)]
    if not same:
        return None
    if group_id:
        members = set(await student_group_repo.get_students_for_group(group_id))
        in_group = [s for s in same if s.student_id in members]
        if in_group:
            return in_group[0]
    return same[0] if len(same) == 1 else None


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

    dup = await open_duplicate(pending_repo, name, group.group_id)
    if dup is not None:
        logger.info("Новый ребёнок по ссылке: %s в %s от %s — заявка %s уже открыта, повтор не плодим",
                    name, group.group_id, fmt_addr(addr), dup.action_id)
        return NewChildRequest(dup, duplicate=True)
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
    return NewChildRequest(action)


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
    """Завести карточку, добавить в группу, привязать родителя. None — заявку уже решили.
    Ученик с таким именем уже есть (`existing_match`) — вторую карточку не заводим: родитель привязывается
    к существующей, в группу добавляется, если ещё не в ней (21.10.2026)."""
    if not await pending_repo.claim(action.action_id, DONE, decided_by):
        return None
    gid = group_of(action)
    student = await existing_match(student_repo, student_group_repo, action.student_name, gid)
    existing, added = student is not None, False
    if student is None:
        student = await student_repo.add(name=action.student_name)
    if gid and (not existing or gid not in await student_group_repo.get_groups_for_student(student.student_id)):
        await student_group_repo.add(student.student_id, gid)
        added = True
    addr = parse_addr(action.parent_addr) if action.parent_addr else None
    if addr is not None:
        await student_repo.add_parent(student.student_id, addr)
    await activity.record(activity.STUDENT,
                          (f"Заявка родителя: ученик {student.student_id} уже в базе — родитель привязан · {gid}"
                           if existing else f"Новый ученик по заявке родителя: {student.student_id} · {gid}"),
                          actor=decided_by, ref=student.student_id)
    if notifier is not None and addr is not None:
        from config.settings import settings
        from bot.screens.parent_menu import menu_rows
        try:
            await notifier.send(
                addr, f"✅ Ребёнок <b>{student.name}</b> {'найден в базе' if existing else 'добавлен в группу'}, "
                      "вы привязаны к нему.\n\nВыберите раздел:",
                rows=menu_rows(platform=addr[0], receipt_email=settings.parent_receipt_email),
            )
        except Exception as exc:
            logger.warning("Новый ребёнок: родителю %s не ушло: %s", action.parent_addr, exc)
    logger.info("Новый ребёнок %s %s по заявке %s (решил %s)", student.student_id,
                "привязан к существующей карточке" if existing else "заведён", action.action_id, decided_by)
    return ApprovedChild(student, existing, added)


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
