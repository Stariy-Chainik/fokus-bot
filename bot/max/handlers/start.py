"""Вход родителя в MAX: /start, ссылка группы, регистрация по фамилии, второй ребёнок."""
from __future__ import annotations
import logging

from maxapi import F
from maxapi.types import BotStarted, MessageCreated, MessageCallback, CommandStart

from config.settings import settings
from bot.screens import cb
from bot.screens.adapters import to_aiogram_markup
from bot.screens.parent_menu import menu_rows
from bot.services.parent_notifier import resolve_notifier, fmt_addr, max_addr
from bot.utils.group_links import parse_start_payload
from bot.keyboards.client import kb_admin_approve_child
from bot.utils.notify import notify
from ..render import send_screen, edit_screen, alert
from ..states import MaxParentStates
from . import router
from ._common import parent_students, show_menu

logger = logging.getLogger(__name__)


def _surname_matches(student_name: str, query: str) -> bool:
    return student_name.lower().startswith(query.lower())


def _match_rows(matches: list, cb_prefix: str, retry_cb: str) -> list:
    rows = [[cb(s.name, f"{cb_prefix}:{s.student_id}")] for s in matches[:10]]
    rows.append([cb("❌ Нет в списке", retry_cb)])
    return rows


async def _group_screen(group_id: str, student_repo, student_group_repo, group_repo, branch_repo):
    group = await group_repo.get_by_id(group_id)
    if group is None:
        return None
    branch = await branch_repo.get_by_id(group.branch_id)
    ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted((s for s in await student_repo.get_all() if s.student_id in ids), key=lambda s: s.name.lower())
    rows = [[cb(s.name, f"glink:{group_id}:{s.student_id}")] for s in students]
    rows.append([cb("❌ Моего ребёнка нет в списке", f"glink_none:{group_id}")])
    branch_line = f"\nФилиал: {branch.name}" if branch else ""
    return f"🩰 Группа «{group.name}»{branch_line}\n\nВыберите вашего ребёнка:", rows


async def _entry(bot, uid: int, payload: str, context, student_repo, student_group_repo, group_repo, branch_repo) -> None:
    await context.clear()
    secret = settings.group_link_secret or settings.bot_token
    group_id = parse_start_payload(payload, secret) if payload else None
    if group_id:
        screen = await _group_screen(group_id, student_repo, student_group_repo, group_repo, branch_repo)
        if screen is None:
            await send_screen(bot, uid, "Группа не найдена. Обратитесь к педагогу за новой ссылкой.")
            return
        await send_screen(bot, uid, *screen)
        return
    students = await parent_students(student_repo, uid)
    if students:
        from bot.screens.parent_menu import welcome_text
        await send_screen(bot, uid, welcome_text(students), menu_rows(platform="max"))
        return
    await send_screen(bot, uid, "Добро пожаловать!\n\nВведите фамилию ученика для регистрации:")


@router.bot_started()
async def on_bot_started(event: BotStarted, context, max_uid, student_repo, student_group_repo, group_repo, branch_repo):
    await _entry(event.bot, max_uid, event.payload or "", context, student_repo, student_group_repo, group_repo, branch_repo)


@router.message_created(CommandStart())
async def on_start_cmd(event: MessageCreated, context, max_uid, student_repo, student_group_repo, group_repo, branch_repo):
    text = (event.message.body.text or "").strip()
    payload = text.split(maxsplit=1)[1] if " " in text else ""
    await _entry(event.bot, max_uid, payload, context, student_repo, student_group_repo, group_repo, branch_repo)


# ─── Ссылка группы ───────────────────────────────────────────────────────────

@router.message_callback(F.callback.payload.startswith("glink:"))
async def on_group_pick(event: MessageCallback, max_uid, student_repo, group_repo, user_repo, tg_bot):
    _, group_id, student_id = event.callback.payload.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await alert(event, "Ученик не найден")
        return
    if max_uid in student.parent_max_ids:
        await show_menu(event, await parent_students(student_repo, max_uid))
        return
    prev_addrs = list(student.parent_addrs)
    await student_repo.add_parent_max_id(student_id, max_uid)
    logger.info("MAX: привязка по ссылке группы %s: max_id=%s → %s", group_id, max_uid, student_id)
    group = await group_repo.get_by_id(group_id)
    sender = event.callback.user
    name = f"{sender.first_name} {sender.last_name or ''}".strip()
    admins = [u.tg_id for u in await user_repo.get_admins()]
    undo_rows = [[cb("🚫 Отменить привязку", f"glink_undo:{student_id}:{fmt_addr(max_addr(max_uid))}")]]
    await notify(tg_bot, admins,
                 f"🔗 <b>Привязка по ссылке группы (MAX)</b>\n\n"
                 f"Родитель: {name} (MAX <code>{max_uid}</code>)\n"
                 f"Ученик: <b>{student.name}</b>\nГруппа: {group.name if group else group_id}",
                 reply_markup=to_aiogram_markup(undo_rows))
    await resolve_notifier(tg_bot).send_many(
        prev_addrs,
        f"ℹ️ К вашему ребёнку <b>{student.name}</b> привязался {name} (MAX).\n"
        f"Если это не член семьи — сообщите администратору.",
    )
    await edit_screen(event, f"✅ Вы привязаны к ученику <b>{student.name}</b>", [])
    await show_menu(event, await parent_students(student_repo, max_uid), new_message=True)


@router.message_callback(F.callback.payload.startswith("glink_none:"))
async def on_group_none(event: MessageCallback, max_uid, group_repo, user_repo, tg_bot):
    group_id = event.callback.payload.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    sender = event.callback.user
    name = f"{sender.first_name} {sender.last_name or ''}".strip()
    await notify(tg_bot, [u.tg_id for u in await user_repo.get_admins()],
                 f"❓ Родитель {name} (MAX <code>{max_uid}</code>) не нашёл своего ребёнка "
                 f"в группе «{group.name if group else group_id}».")
    await edit_screen(event, "Сообщили администратору — с вами свяжутся.", [])


# ─── Регистрация по фамилии (первый ребёнок) ─────────────────────────────────

@router.message_created(F.message.body.text, None)
async def on_text_no_state(event: MessageCreated, max_uid, student_repo):
    text = (event.message.body.text or "").strip()
    if text.startswith("/"):
        return
    students = await parent_students(student_repo, max_uid)
    if students:
        await show_menu(event, students, new_message=True)
        return
    if len(text) < 2:
        await event.message.answer("Введите фамилию ученика (минимум 2 символа):")
        return
    matches = [s for s in await student_repo.get_all() if _surname_matches(s.name, text)]
    if not matches:
        await event.message.answer(f"Ученик с фамилией <b>{text}</b> не найден.\n\nПопробуйте ещё раз:")
        return
    if len(matches) == 1:
        s = matches[0]
        rows = [[cb("✅ Да, это мой ребёнок", f"client_reg:{s.student_id}")], [cb("❌ Другой ученик", "client_reg_retry")]]
        await send_screen(event.bot, max_uid, f"Нашли: <b>{s.name}</b>\n\nЭто ваш ребёнок?", rows)
    else:
        await send_screen(event.bot, max_uid, f"Найдено несколько учеников с фамилией <b>{text}</b>. Выберите своего:",
                          _match_rows(matches, "client_reg", "client_reg_retry"))


@router.message_callback(F.callback.payload.startswith("client_reg:"))
async def on_reg_confirm(event: MessageCallback, max_uid, student_repo):
    student_id = event.callback.payload.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await alert(event, "Ученик не найден")
        return
    if max_uid not in student.parent_max_ids:
        await student_repo.add_parent_max_id(student_id, max_uid)
        logger.info("MAX: родитель max_id=%s привязан к %s", max_uid, student_id)
    await edit_screen(event, f"✅ Вы привязаны к ученику <b>{student.name}</b>", [])
    await show_menu(event, await parent_students(student_repo, max_uid), new_message=True)


@router.message_callback(F.callback.payload == "client_reg_retry")
async def on_reg_retry(event: MessageCallback):
    await edit_screen(event, "Введите фамилию ученика:", [])


# ─── Второй ребёнок — заявка админу ─────────────────────────────────────────

@router.message_callback(F.callback.payload == "client:add_child")
async def on_add_child(event: MessageCallback, context, max_uid, student_repo):
    if not await parent_students(student_repo, max_uid):
        await alert(event, "Сначала привяжите первого ребёнка")
        return
    await context.set_state(MaxParentStates.adding_child)
    await edit_screen(event, "Введите фамилию ученика:", [[cb("« Отмена", "go:home")]])


@router.message_created(F.message.body.text, MaxParentStates.adding_child)
async def on_add_child_text(event: MessageCreated, max_uid, student_repo):
    text = (event.message.body.text or "").strip()
    if len(text) < 2:
        await event.message.answer("Введите фамилию ученика (минимум 2 символа):")
        return
    matches = [s for s in await student_repo.get_all() if _surname_matches(s.name, text)]
    if not matches:
        await event.message.answer(f"Ученик с фамилией <b>{text}</b> не найден.\n\nПопробуйте ещё раз:")
        return
    if len(matches) == 1:
        s = matches[0]
        rows = [[cb(f"✅ {s.name}", f"client_add_req:{s.student_id}")], [cb("❌ Другой ученик", "client_add_retry")]]
        await send_screen(event.bot, max_uid, f"Нашли: <b>{s.name}</b>\n\nОтправить заявку администратору?", rows)
    else:
        await send_screen(event.bot, max_uid, "Найдено несколько учеников. Выберите нужного:",
                          _match_rows(matches, "client_add_req", "client_add_retry"))


@router.message_callback(F.callback.payload == "client_add_retry")
async def on_add_retry(event: MessageCallback, context):
    await context.set_state(MaxParentStates.adding_child)
    await edit_screen(event, "Введите фамилию ученика:", [[cb("« Отмена", "go:home")]])


@router.message_callback(F.callback.payload.startswith("client_add_req:"))
async def on_add_request(event: MessageCallback, context, max_uid, student_repo, user_repo, tg_bot):
    student_id = event.callback.payload.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    await context.clear()
    if student is None:
        await alert(event, "Ученик не найден")
        return
    if max_uid in student.parent_max_ids:
        await show_menu(event, await parent_students(student_repo, max_uid))
        return
    sender = event.callback.user
    name = f"{sender.first_name} {sender.last_name or ''}".strip()
    addr = fmt_addr(max_addr(max_uid))
    await notify(tg_bot, [u.tg_id for u in await user_repo.get_admins()],
                 f"👤 <b>Запрос на привязку (MAX)</b>\n\nКлиент: {name} (MAX <code>{max_uid}</code>)\n"
                 f"Ученик: <b>{student.name}</b>",
                 reply_markup=kb_admin_approve_child(addr, student_id))
    logger.info("MAX: заявка на второго ребёнка max_id=%s → %s", max_uid, student_id)
    await edit_screen(event, "✅ Заявка отправлена администратору.\n\nОжидайте подтверждения.", [[cb("« Меню", "go:home")]])


# ─── Меню ────────────────────────────────────────────────────────────────────

@router.message_callback(F.callback.payload == "go:home")
async def on_home(event: MessageCallback, context, max_uid, student_repo):
    await context.clear()
    students = await parent_students(student_repo, max_uid)
    if not students:
        await edit_screen(event, "Введите фамилию ученика для регистрации:", [])
        return
    await show_menu(event, students)
