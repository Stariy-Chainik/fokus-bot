from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository,
    GroupRepository, BranchRepository, StudentGroupRepository,
)
from bot.services import (
    StudentService,
)
from bot.keyboards.admin import (
    kb_confirm,
    kb_back,
)
from bot.handlers.filters import AdminOnly

from bot.services.rosters import group_members
from ._base import router

logger = logging.getLogger(__name__)


# ─── Удаление ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "students:delete", AdminOnly())
async def cb_delete_student_start(
    callback: CallbackQuery, user: User, branch_repo: BranchRepository,
) -> None:
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


@router.callback_query(F.data.startswith("del_st_brn:"), AdminOnly())
async def cb_delete_student_branch(
    callback: CallbackQuery, user: User, group_repo: GroupRepository,
) -> None:
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


@router.callback_query(F.data.startswith("del_st_grp:"), AdminOnly())
async def cb_delete_student_group(
    callback: CallbackQuery, user: User,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    back_cb = f"del_st_brn:{group.branch_id}" if group else "students:delete"

    grp_students = await group_members(student_repo, student_group_repo, group_id)
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


@router.callback_query(F.data.startswith("del_student:"), AdminOnly())
async def cb_delete_student_confirm(
    callback: CallbackQuery, user: User, student_repo: StudentRepository,
) -> None:
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


@router.callback_query(F.data.startswith("confirm_del_student:"), AdminOnly())
async def cb_delete_student_do(
    callback: CallbackQuery,
    user: User,
    student_service: StudentService,
) -> None:
    _, group_id, student_id = callback.data.split(":", 2)
    ok = await student_service.delete_student(student_id)
    text = f"Ученик {student_id} удалён." if ok else "Ученик не найден."
    await callback.message.edit_text(
        text, reply_markup=kb_back(f"del_st_grp:{group_id}"),
    )
    await callback.answer()



