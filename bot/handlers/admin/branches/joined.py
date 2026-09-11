"""Месяц вступления ученика в абонементную группу.

Абонемент начисляется начиная с этого месяца, поэтому добавление ученика задним
числом не создаёт долг за месяцы, когда он ещё не занимался. Пустое значение —
«с начала группы» (прежнее поведение).
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


async def _render_joined(
    message, group_id: str, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    group = await group_repo.get_by_id(group_id)
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    joined = await student_group_repo.get_joined_map()
    students = sorted((s for s in await student_repo.get_all() if s.student_id in member_ids),
                      key=lambda s: s.name.lower())
    rows = [[InlineKeyboardButton(
        text=f"{s.name} — {_fmt(joined.get((s.student_id, group_id), ''))}",
        callback_data=f"gjset:{group_id}:{s.student_id}",
    )] for s in students]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    await message.edit_text(
        f"📅 <b>Месяц вступления — {group.name if group else group_id}</b>\n\n"
        f"Абонемент начисляется начиная с этого месяца.\n"
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
    joined = (await student_group_repo.get_joined_map()).get((student_id, group_id), "")
    rows = []
    for period in last_periods(_MONTHS_TO_PICK):
        mark = "✅ " if period == joined else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{display_period(period)}", callback_data=f"gjdo:{group_id}:{student_id}:{period}")])
    rows.append([InlineKeyboardButton(
        text=("✅ " if not joined else "") + "с начала группы",
        callback_data=f"gjdo:{group_id}:{student_id}:-")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_joined:{group_id}")])
    await callback.message.edit_text(
        f"📅 <b>{student.name if student else student_id}</b>\n"
        f"Сейчас: {_fmt(joined)}\n\nС какого месяца начислять абонемент?",
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
