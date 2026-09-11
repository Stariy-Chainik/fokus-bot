"""Месяцы членства ученика в абонементной группе: вступление и уход.

Абонемент начисляется с месяца вступления и до месяца ухода (сам месяц ухода уже
не оплачивается). Пустые значения — «с начала группы» и «не ушёл». Ушедшие
остаются в таблице, чтобы прошлые месяцы не пропали из счёта.
"""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import GroupBillingMode
from bot.repositories import GroupRepository, StudentRepository, StudentGroupRepository
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period, last_periods
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)

_MONTHS_TO_PICK = 12


def _fmt(period: str) -> str:
    return display_period(period) if period else "с начала группы"


def _label(row) -> str:
    """Подпись членства: «с 04.2026» / «04.2026 — ушёл с 09.2026»."""
    if row is None:
        return "с начала группы"
    base = _fmt(row.joined_period)
    return f"{base} · ушёл с {display_period(row.left_period)}" if row.left_period else base


async def _render_joined(
    message, group_id: str, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    group = await group_repo.get_by_id(group_id)
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted((s for s in await student_repo.get_all() if s.student_id in member_ids),
                      key=lambda s: s.name.lower())
    rows = [[InlineKeyboardButton(
        text=f"{s.name} — {_label(membership.get((s.student_id, group_id)))}",
        callback_data=f"gjset:{group_id}:{s.student_id}",
    )] for s in students]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    await message.edit_text(
        f"📅 <b>Месяцы членства — {group.name if group else group_id}</b>\n\n"
        f"Абонемент начисляется с месяца вступления и до месяца ухода.\n"
        f"Выберите ученика, чтобы изменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("group_joined:"))
async def cb_group_joined(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await _render_joined(callback.message, group_id, group_repo, student_repo, student_group_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("gjset:"))
async def cb_joined_pick_month(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    row = (await student_group_repo.get_membership_map()).get((student_id, group_id))
    joined = row.joined_period if row else ""
    rows = []
    for period in last_periods(_MONTHS_TO_PICK):
        mark = "✅ " if period == joined else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{display_period(period)}", callback_data=f"gjdo:{group_id}:{student_id}:{period}")])
    rows.append([InlineKeyboardButton(
        text=("✅ " if not joined else "") + "с начала группы",
        callback_data=f"gjdo:{group_id}:{student_id}:-")])
    left = row.left_period if row else ""
    if left:
        rows.append([InlineKeyboardButton(
            text=f"↩️ Вернуть в группу (сейчас ушёл с {display_period(left)})",
            callback_data=f"gldo:{group_id}:{student_id}:-")])
    else:
        for period in last_periods(3):
            rows.append([InlineKeyboardButton(
                text=f"🚪 Ушёл с {display_period(period)}",
                callback_data=f"gldo:{group_id}:{student_id}:{period}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_joined:{group_id}")])
    await callback.message.edit_text(
        f"📅 <b>{student.name if student else student_id}</b>\n"
        f"Сейчас: {_label(row)}\n\nС какого месяца начислять абонемент?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gjdo:"))
async def cb_joined_apply(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id, period = callback.data.split(":", 3)
    value = "" if period == "-" else period
    ok = await student_group_repo.set_joined_period(student_id, group_id, value)
    student = await student_repo.get_by_id(student_id)
    if ok:
        logger.info("Админ %s: месяц вступления %s в %s → %r",
                    callback.from_user.id, student_id, group_id, value)
        await callback.answer(f"✅ {student.name if student else student_id}: {_fmt(value)}")
    else:
        await callback.answer("Не удалось изменить", show_alert=True)
    await _render_joined(callback.message, group_id, group_repo, student_repo, student_group_repo)


@router.callback_query(F.data.startswith("gldo:"))
async def cb_left_apply(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    """Пометить уход из группы или вернуть обратно (значение «-» снимает пометку)."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id, period = callback.data.split(":", 3)
    value = "" if period == "-" else period
    ok = await student_group_repo.set_left_period(student_id, group_id, value)
    student = await student_repo.get_by_id(student_id)
    who = student.name if student else student_id
    if ok:
        logger.info("Админ %s: уход %s из %s → %r", callback.from_user.id, student_id, group_id, value)
        await callback.answer(f"✅ {who}: " + (f"ушёл с {display_period(value)}" if value else "снова в группе"))
    else:
        await callback.answer("Не удалось изменить", show_alert=True)
    await _render_joined(callback.message, group_id, group_repo, student_repo, student_group_repo)
