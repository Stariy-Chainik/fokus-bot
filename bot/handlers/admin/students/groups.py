from __future__ import annotations
import re
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, Student, StudentRequest, GroupBillingMode, StudentGroupTier
from bot.repositories import (
    StudentRepository,
    GroupRepository, BranchRepository, StudentGroupRepository,
    StudentRequestRepository, ClientRepository,
)
from bot.services import (
    StudentService, TierToggleError,
    StudentRequestService, LinkExistingOutcome,
)
from bot.models.enums import RequestStatus
from bot.states import AddStudentStates, StudentListStates, PartnerAssignStates, ClientCreateStates
from bot.handlers.common import show_card
from bot.keyboards.admin import (
    kb_students_menu,
    kb_student_paged, kb_student_card, kb_partner_candidates,
    kb_confirm, kb_back, _STUDENT_PAGE_SIZE,
)
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)
from ._base import _render_student_card


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
    rows: list = []
    for gid in gids:
        g = await group_repo.get_by_id(gid)
        if g:
            branch = await branch_repo.get_by_id(g.branch_id)
            bname = branch.name if branch else "—"
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
    _, student_id, group_id = callback.data.split(":", 2)
    await student_group_repo.remove(student_id, group_id)
    group = await group_repo.get_by_id(group_id)
    toast = f"Убран из группы «{group.name}»" if group else "Убран из группы"
    await callback.answer(toast, show_alert=False)
    await _render_student_card(callback, student_id, "students:list", student_service)


