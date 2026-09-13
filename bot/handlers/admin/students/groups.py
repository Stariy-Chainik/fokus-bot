from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    GroupRepository,
    BranchRepository, StudentGroupRepository,
)
from bot.services import (
    StudentService,
)
from bot.keyboards.admin import (
    kb_back,
)
from bot.handlers.access import is_admin as _is_admin
from bot.services.membership import is_subscription, leave_group, leave_options
from bot.utils.dates import current_period

from ._base import router
from ._base import _render_student_card

logger = logging.getLogger(__name__)


# ─── Управление группами ученика ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("student_groups_add:"))
async def cb_student_groups_add_branches(
    callback: CallbackQuery, user: User | None,
    branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    if not branches:
        await callback.message.edit_text(
            "Филиалов нет.", reply_markup=kb_back(f"student_card:{student_id}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"🏢 {b.name}",
            callback_data=f"sg_add_brn:{student_id}:{b.branch_id}",
        )]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"student_card:{student_id}")])
    await callback.message.edit_text(
        "<b>Добавить в группу — выберите филиал:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sg_add_brn:"))
async def cb_student_groups_add_pick_group(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, branch_id = callback.data.split(":", 2)
    groups = sorted(
        [g for g in await group_repo.get_all() if g.branch_id == branch_id],
        key=lambda g: (g.sort_order, g.name),
    )
    current = set(await student_group_repo.get_groups_for_student(student_id))
    available = [g for g in groups if g.group_id not in current]
    if not available:
        await callback.message.edit_text(
            "В этом филиале нет групп, в которых ученика ещё нет.",
            reply_markup=kb_back(f"student_groups_add:{student_id}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"💃 {g.name}",
            callback_data=f"sg_add_do:{student_id}:{g.group_id}",
        )]
        for g in available
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"student_groups_add:{student_id}")])
    await callback.message.edit_text(
        "<b>Выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sg_add_do:"))
async def cb_student_groups_add_do(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, group_id = callback.data.split(":", 2)
    await student_group_repo.add(student_id, group_id)
    group = await group_repo.get_by_id(group_id)
    toast = f"Добавлен в группу «{group.name}»" if group else "Добавлен в группу"
    await callback.answer(toast, show_alert=False)
    await _render_student_card(callback, student_id, "students:list", student_service)


@router.callback_query(F.data.startswith("student_groups_remove:"))
async def cb_student_groups_remove_list(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    gids = await student_group_repo.get_groups_for_student(student_id)
    if not gids:
        await callback.answer("У ученика нет групп", show_alert=True)
        return
    groups_by_id = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    rows: list = []
    for gid in gids:
        g = groups_by_id.get(gid)
        if g:
            bname = branches.get(g.branch_id, "—")
            label = f"{g.name} ({bname})"
        else:
            label = gid
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f"sg_rm_do:{student_id}:{gid}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"student_card:{student_id}")])
    await callback.message.edit_text(
        "<b>Убрать из группы — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sg_rm_do:"))
async def cb_student_groups_remove_do(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    student_id, group_id = parts[1], parts[2]
    left_period = parts[3] if len(parts) > 3 else ""
    group = await group_repo.get_by_id(group_id)

    # Абонемент: спрашиваем, с какого месяца прекращать начисление
    if not left_period and await is_subscription(group_id, group_repo):
        rows = [[InlineKeyboardButton(text=label, callback_data=f"sg_rm_do:{student_id}:{group_id}:{period}")]
                for period, label in leave_options()]
        rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"student_card:{student_id}")])
        await callback.message.edit_text(
            f"<b>Убрать из «{group.name if group else group_id}»</b>\n\n"
            f"Это абонементная группа. Прошлые месяцы останутся в счёте.\n"
            f"С какого месяца прекратить начисление?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return

    result = await leave_group(student_id, group_id, left_period or current_period(),
                               group_repo, student_group_repo)
    name = f"«{group.name}»" if group else "группы"
    toast = (f"Помечен ушедшим из {name} с {left_period}" if result == "marked"
             else f"Убран из {name}" if result == "removed" else "Не удалось убрать")
    await callback.answer(toast, show_alert=False)
    await _render_student_card(callback, student_id, "students:list", student_service)


