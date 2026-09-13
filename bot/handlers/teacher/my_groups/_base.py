"""Педагог: «Группы» — просмотр своих групп, управление составом."""
from __future__ import annotations
import logging

from aiogram import Router
from aiogram.types import MaybeInaccessibleMessage, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models.enums import GroupBillingMode
from bot.utils.groups import hide_service_groups
from bot.repositories import (
    StudentRepository, GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository,
)
from bot.handlers.common import show_card

from bot.services.rosters import group_members
logger = logging.getLogger(__name__)
router = Router(name="teacher_my_groups")




async def _owns_group(teacher_id: str, group_id: str, tg_repo: TeacherGroupRepository) -> bool:
    gids = hide_service_groups(await tg_repo.get_groups_for_teacher(teacher_id))
    return group_id in gids


def _normalize(q: str) -> str:
    return " ".join((q or "").strip().split()).lower()


def _kb_t_group_card(group, students: list) -> InlineKeyboardMarkup:
    group_id = group.group_id
    rows = [
        [InlineKeyboardButton(
            text=f"👤 {s.name}",
            callback_data=f"t_student_card:{s.student_id}",
        )]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить ученика", callback_data=f"t_grp_add:{group_id}")])
    if students:
        rows.append([InlineKeyboardButton(text="➖ Убрать ученика", callback_data=f"t_grp_rm:{group_id}")])
    if group.billing_mode == GroupBillingMode.PER_VISIT:
        rows.append([InlineKeyboardButton(text="📊 Посещаемость", callback_data=f"t_grp_attendance:{group_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_groups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_t_group_card(
    message: MaybeInaccessibleMessage | None, group_id: str,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    group = await group_repo.get_by_id(group_id)
    if not group:
        await message.edit_text("Группа не найдена")
        return
    branch = await branch_repo.get_by_id(group.branch_id)
    branch_name = branch.name if branch else "—"
    members = await group_members(student_repo, student_group_repo, group_id)
    text = (
        f"<b>🏢 {branch_name} / {group.name}</b>\n\n"
        f"Учеников: {len(members)}"
    )
    await show_card(
        message, text,
        reply_markup=_kb_t_group_card(group, members),
    )



