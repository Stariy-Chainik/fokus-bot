"""Вход родителя по ссылке группы: t.me/<bot>?start=g_<group_id>_<token>.

Педагог отправляет ссылку в родительский чат группы. Родитель тапает,
видит список учеников ЭТОЙ группы, выбирает ребёнка — привязка готова.
Код группы в ссылке — проверочный фактор (посторонний без ссылки не
увидит ничего), поэтому привязка проходит без одобрения администратора,
но администраторы получают уведомление с кнопкой отмены; уже привязанные
родители того же ученика — тоже.

После привязки бот просит поделиться телефоном (нужен для фискальных
чеков) — шаг можно пропустить.
"""
from __future__ import annotations

import logging
import re

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    MaybeInaccessibleMessage,
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from aiogram.exceptions import TelegramAPIError

from config.settings import settings
from bot.models import User
from bot.repositories import (
    StudentRepository, StudentGroupRepository, GroupRepository,
    BranchRepository, UserRepository, ClientRepository,
)
from bot.keyboards.client import kb_client_menu
from bot.states.client_states import GroupLinkStates
from bot.utils.group_links import parse_start_payload
from bot.services.parent_notifier import resolve_notifier, parse_addr

logger = logging.getLogger(__name__)
router = Router(name="client_group_link")

_SKIP_TEXT = "Пропустить"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _link_secret() -> str:
    return settings.group_link_secret or settings.bot_token


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return f"+{digits}" if digits else ""


def _surname_soft_match(student_name: str, tg_user) -> bool:
    """Мягкая сверка: фамилия ученика (первое слово) встречается в имени TG-профиля."""
    surname = (student_name.split() or [""])[0].lower()
    if len(surname) < 3:
        return True  # слишком короткая фамилия — не сигналим
    profile = f"{tg_user.full_name} {tg_user.username or ''}".lower()
    # Отбрасываем последние 1-2 буквы: Иванова/Иванов, Черба/Черб и т.п.
    return surname[: max(3, len(surname) - 2)] in profile


async def _render_group_screen(
    group_id: str,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> tuple[str, InlineKeyboardMarkup] | None:
    group = await group_repo.get_by_id(group_id)
    if group is None:
        return None
    branch = await branch_repo.get_by_id(group.branch_id)
    student_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted(
        (s for s in await student_repo.get_all() if s.student_id in student_ids),
        key=lambda s: s.name.lower(),
    )
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"glink:{group_id}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(
        text="❌ Моего ребёнка нет в списке", callback_data=f"glink_none:{group_id}",
    )])
    branch_line = f"\nФилиал: {branch.name}" if branch else ""
    text = (
        f"🩰 Группа «{group.name}»{branch_line}\n\n"
        f"Выберите вашего ребёнка:"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# ─── Вход по ссылке ───────────────────────────────────────────────────────────

@router.message(CommandStart(deep_link=True))
async def cmd_start_group_link(
    message: Message,
    command: CommandObject,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    group_id = parse_start_payload(command.args or "", _link_secret())
    if group_id is None:
        logger.info("Невалидный start-payload %r от tg_id=%s", command.args, message.from_user.id)
        await message.answer(
            "Ссылка недействительна или устарела.\n\n"
            "Введите фамилию ученика для регистрации:",
        )
        return

    screen = await _render_group_screen(
        group_id, student_repo, student_group_repo, group_repo, branch_repo,
    )
    if screen is None:
        await message.answer("Группа не найдена. Обратитесь к педагогу за новой ссылкой.")
        return
    text, kb = screen
    await message.answer(text, reply_markup=kb)


# ─── Выбор ребёнка → привязка ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("glink:"))
async def cb_group_link_pick(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    group_repo: GroupRepository,
    user_repo: UserRepository,
) -> None:
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    tg_id = callback.from_user.id
    if tg_id in student.parent_tg_ids:
        await callback.message.edit_text(
            f"Вы уже привязаны к ученику <b>{student.name}</b>.\n\nВыберите раздел:",
            reply_markup=kb_client_menu(),
        )
        await callback.answer()
        return

    prev_parent_addrs = list(student.parent_addrs)
    await student_repo.add_parent_tg_id(student_id, tg_id)
    logger.info(
        "Привязка по ссылке группы %s: tg_id=%s → %s", group_id, tg_id, student_id,
    )

    # Уведомления: админам (с кнопкой отмены) и уже привязанным родителям.
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    sender = callback.from_user
    username = f" @{sender.username}" if sender.username else ""
    mismatch = "" if _surname_soft_match(student.name, sender) else (
        "\n⚠️ Имя профиля не похоже на фамилию ученика"
    )
    admin_text = (
        f"🔗 <b>Привязка по ссылке группы</b>\n\n"
        f"Родитель: {sender.full_name}{username} (<code>{tg_id}</code>)\n"
        f"Ученик: <b>{student.name}</b>\n"
        f"Группа: {group_name}{mismatch}"
    )
    kb_undo = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚫 Отменить привязку",
            callback_data=f"glink_undo:{student_id}:{tg_id}",
        ),
    ]])
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    for admin in admins:
        try:
            await callback.bot.send_message(admin.tg_id, admin_text, reply_markup=kb_undo)
        except TelegramAPIError as exc:
            logger.warning("Не доставлено админу tg_id=%s: %s", admin.tg_id, exc)
    await resolve_notifier(callback.bot).send_many(
        prev_parent_addrs,
        f"ℹ️ К вашему ребёнку <b>{student.name}</b> привязался "
        f"{sender.full_name}{username}.\n"
        f"Если это не член семьи — сообщите администратору.",
    )

    # Телефон не спрашиваем (чеки уходят на email) — сразу необязательный email.
    await state.update_data(glink_student_id=student_id)
    kb_self_undo = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="↩️ Я ошибся(лась), отменить",
            callback_data=f"glink_self_undo:{group_id}:{student_id}",
        ),
    ]])
    await callback.message.edit_text(
        f"✅ Вы привязаны к ученику <b>{student.name}</b>",
        reply_markup=kb_self_undo,
    )
    await _ask_email(callback.message, state, None)
    await callback.answer()


@router.callback_query(F.data.startswith("glink_none:"))
async def cb_group_link_none(
    callback: CallbackQuery,
    group_repo: GroupRepository,
    user_repo: UserRepository,
) -> None:
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    sender = callback.from_user
    username = f" @{sender.username}" if sender.username else ""
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id,
                f"❓ Родитель {sender.full_name}{username} (<code>{sender.id}</code>) "
                f"не нашёл своего ребёнка в группе «{group_name}».\n"
                f"Возможно, состав группы в таблице неполный.",
            )
        except TelegramAPIError:
            pass
    await callback.message.edit_text(
        "Сообщили администратору — с вами свяжутся.\n\n"
        "Если ребёнок занимается в другой группе, откройте ссылку этой группы "
        "из её родительского чата.",
    )
    await callback.answer()


# ─── Отмена привязки ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("glink_self_undo:"))
async def cb_group_link_self_undo(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    _, group_id, student_id = callback.data.split(":", 2)
    removed = await student_repo.remove_parent_tg_id(student_id, callback.from_user.id)
    if removed:
        logger.info(
            "Родитель tg_id=%s сам отменил привязку к %s", callback.from_user.id, student_id,
        )
    await state.clear()
    screen = await _render_group_screen(
        group_id, student_repo, student_group_repo, group_repo, branch_repo,
    )
    if screen is None:
        await callback.message.edit_text("Привязка отменена.")
        await callback.answer()
        return
    text, kb = screen
    await callback.message.edit_text(f"Привязка отменена.\n\n{text}", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("glink_undo:"))
async def cb_group_link_admin_undo(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if user is None or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, addr_raw = callback.data.split(":", 2)
    addr = parse_addr(addr_raw)
    removed = addr is not None and await student_repo.remove_parent(student_id, addr)
    if not removed:
        await callback.answer("Привязка уже отменена", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id
    logger.info("Админ %s отменил привязку %s к %s", callback.from_user.id, addr_raw, student_id)
    await resolve_notifier(callback.bot).send(
        addr,
        f"Ваша привязка к ученику <b>{student_name}</b> отменена администратором.\n"
        f"Если это ошибка — свяжитесь со школой.",
    )
    await callback.message.edit_text(
        f"{callback.message.html_text}\n\n🚫 <b>Привязка отменена</b>",
    )
    await callback.answer("Привязка отменена")


# ─── Телефон ─────────────────────────────────────────────────────────────────

@router.message(GroupLinkStates.waiting_contact, F.contact)
async def on_contact_shared(
    message: Message,
    state: FSMContext,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
) -> None:
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(
            "Пожалуйста, отправьте свой номер кнопкой «📱 Поделиться номером».",
        )
        return

    phone = _normalize_phone(contact.phone_number)
    data = await state.get_data()
    student_id = data.get("glink_student_id")
    await state.clear()

    tg_id = message.from_user.id
    client = await client_repo.get_by_tg_id(tg_id)
    if client is None:
        client = await client_repo.create(
            name=message.from_user.full_name or str(tg_id),
            created_by_tg_id=tg_id,
            phone=phone,
            tg_id=tg_id,
        )
        logger.info("Создан клиент %s (tg_id=%s, телефон получен)", client.client_id, tg_id)
    elif phone and client.phone != phone:
        await client_repo.update_phone(client.client_id, phone)
        logger.info("Обновлён телефон клиента %s", client.client_id)

    # Связываем ученика с карточкой клиента, если он ещё ни к кому не привязан.
    if student_id:
        student = await student_repo.get_by_id(student_id)
        if student is not None and not student.client_id:
            await student_repo.set_client_id(student_id, client.client_id)

    await message.answer("Спасибо! Номер сохранён. ✅", reply_markup=ReplyKeyboardRemove())
    await _ask_email(message, state, client.client_id)


@router.message(GroupLinkStates.waiting_contact, F.text == _SKIP_TEXT)
async def on_contact_skip(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Хорошо, номер можно добавить позже.", reply_markup=ReplyKeyboardRemove(),
    )
    await _ask_email(message, state, None)


async def _ask_email(message: MaybeInaccessibleMessage | None, state: FSMContext, client_id: str | None) -> None:
    """Необязательный шаг: email для фискальных чеков об оплате.

    При PARENT_RECEIPT_EMAIL=false шаг пропускается — сразу меню.
    """
    from config.settings import settings
    if not settings.parent_receipt_email:
        await state.clear()
        await message.answer("Выберите раздел:", reply_markup=kb_client_menu())
        return
    await state.set_state(GroupLinkStates.waiting_email)
    await state.update_data(glink_client_id=client_id)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=_SKIP_TEXT)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await message.answer(
        "Если хотите получать чеки об оплате на почту — отправьте свой email.\n"
        f"Или нажмите «{_SKIP_TEXT}».",
        reply_markup=kb,
    )


@router.message(GroupLinkStates.waiting_email, F.text == _SKIP_TEXT)
async def on_email_skip(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Хорошо. ✅", reply_markup=ReplyKeyboardRemove())
    await message.answer("Выберите раздел:", reply_markup=kb_client_menu())


@router.message(GroupLinkStates.waiting_email, F.text)
async def on_email_entered(
    message: Message,
    state: FSMContext,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
) -> None:
    email = (message.text or "").strip()
    if not _EMAIL_RE.match(email):
        await message.answer(
            f"Не похоже на email. Отправьте адрес вида name@mail.ru или «{_SKIP_TEXT}».",
        )
        return
    data = await state.get_data()
    client_id = data.get("glink_client_id")
    student_id = data.get("glink_student_id")
    await state.clear()

    tg_id = message.from_user.id
    client = None
    if client_id:
        client = await client_repo.get_by_id(client_id)
    if client is None:
        client = await client_repo.get_by_tg_id(tg_id)
    if client is None:
        client = await client_repo.create(
            name=message.from_user.full_name or str(tg_id),
            created_by_tg_id=tg_id, tg_id=tg_id,
        )
        if student_id:
            student = await student_repo.get_by_id(student_id)
            if student is not None and not student.client_id:
                await student_repo.set_client_id(student_id, client.client_id)
    await client_repo.update_email(client.client_id, email)
    logger.info("Email для чеков сохранён: клиент %s", client.client_id)
    await message.answer("Email сохранён — чеки будут приходить на него. ✅",
                         reply_markup=ReplyKeyboardRemove())
    await message.answer("Выберите раздел:", reply_markup=kb_client_menu())


@router.message(GroupLinkStates.waiting_contact, F.text)
async def on_contact_other_text(message: Message) -> None:
    await message.answer(
        "Нажмите «📱 Поделиться номером» на клавиатуре снизу — "
        f"или «{_SKIP_TEXT}», если не хотите оставлять номер.",
    )
