from __future__ import annotations
"""Педагог: «Группы» — просмотр своих групп, управление составом."""
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository,
)

logger = logging.getLogger(__name__)


from bot.handlers.access import is_teacher as _is_teacher



from ._base import router, _owns_group, _render_t_group_card


# ─── Список групп педагога ───────────────────────────────────────────────────

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
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    gid = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, gid, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    await _render_t_group_card(
        callback.message, gid, group_repo, branch_repo, student_repo, student_group_repo,
    )
    await callback.answer()


