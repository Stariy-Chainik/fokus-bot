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


# ─── Удаление ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "students:delete")
async def cb_delete_student_start(
    callback: CallbackQuery, user: User | None, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    if not branches:
        await callback.message.edit_text("Филиалов нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"del_st_brn:{b.branch_id}")]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    await callback.message.edit_text(
        "<b>Удаление ученика — выберите филиал:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_st_brn:"))
async def cb_delete_student_branch(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    groups = sorted(
        [g for g in await group_repo.get_all() if g.branch_id == branch_id],
        key=lambda g: (g.sort_order, g.name),
    )
    if not groups:
        await callback.message.edit_text(
            "В этом филиале нет групп.",
            reply_markup=kb_back("students:delete"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"del_st_grp:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="students:delete")])
    await callback.message.edit_text(
        "<b>Выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_st_grp:"))
async def cb_delete_student_group(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    back_cb = f"del_st_brn:{group.branch_id}" if group else "students:delete"

    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    grp_students = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not grp_students:
        await callback.message.edit_text(
            f"В группе «{group_name}» нет учеников.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    rows = [
        [InlineKeyboardButton(
            text=s.name, callback_data=f"del_student:{group_id}:{s.student_id}",
        )]
        for s in grp_students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    await callback.message.edit_text(
        f"<b>«{group_name}» — выберите ученика для удаления:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_student:"))
async def cb_delete_student_confirm(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить ученика «{student.name}» ({student_id})?</b>",
        reply_markup=kb_confirm(
            f"confirm_del_student:{group_id}:{student_id}",
            f"del_st_grp:{group_id}",
            confirm_text="🗑 Удалить",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_student:"))
async def cb_delete_student_do(
    callback: CallbackQuery,
    user: User | None,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    ok = await student_service.delete_student(student_id)
    text = f"Ученик {student_id} удалён." if ok else "Ученик не найден."
    await callback.message.edit_text(
        text, reply_markup=kb_back(f"del_st_grp:{group_id}"),
    )
    await callback.answer()



