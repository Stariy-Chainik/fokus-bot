from __future__ import annotations
"""Педагог: «Мои группы» — просмотр групп, где преподаёт педагог, и их составов."""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, GroupRepository, BranchRepository, TeacherGroupRepository,
)

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_groups")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


@router.callback_query(F.data == "teacher:my_groups")
async def cb_my_groups(
    callback: CallbackQuery,
    user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    gids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    groups = [g for g in await group_repo.get_all() if g.group_id in gids]

    if not groups:
        await callback.message.edit_text(
            "У вас нет тренировочных групп. Обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
        return

    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups.sort(key=lambda g: (branches.get(g.branch_id, ""), g.name))

    rows = [
        [InlineKeyboardButton(
            text=f"🏢 {branches.get(g.branch_id, '—')} / {g.name}",
            callback_data=f"t_group_card:{g.group_id}",
        )]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])

    await callback.message.edit_text(
        f"Ваши группы ({len(groups)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_group_card:"))
async def cb_t_group_card(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    gid = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(gid)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    branch = await branch_repo.get_by_id(group.branch_id)
    branch_name = branch.name if branch else "—"

    members = sorted(
        [s for s in await student_repo.get_all() if s.group_id == gid],
        key=lambda s: s.name,
    )

    lines = [
        f"<b>🏢 {branch_name} / {group.name}</b>",
        "",
        f"Учеников: {len(members)}",
    ]
    if members:
        lines.append("")
        lines.extend(f"• {s.name}" for s in members)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_groups")],
        ]),
    )
    await callback.answer()
