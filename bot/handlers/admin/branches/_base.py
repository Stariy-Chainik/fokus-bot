from __future__ import annotations
import logging

from aiogram import Router
from aiogram.types import MaybeInaccessibleMessage, InlineKeyboardMarkup, InlineKeyboardButton


from bot.models.enums import GroupBillingMode
from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository, StudentGroupRepository,
)
from bot.keyboards.admin import kb_back
from bot.handlers.common import show_card

from bot.services.rosters import group_members
logger = logging.getLogger(__name__)
router = Router(name="admin_branches")




# ─── Общий рендер карточки группы ─────────────────────────────────────────────

def _kb_group_card(group_id: str, branch_id: str, students: list,
                   subscription: bool = False,
                   archived: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"student_card:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить ученика", callback_data=f"group_add_student:{group_id}")])
    if students:
        rows.append([InlineKeyboardButton(text="➖ Убрать ученика", callback_data=f"group_rm_student:{group_id}")])
    if subscription and students:
        rows.append([InlineKeyboardButton(
            text="📅 Месяц вступления", callback_data=f"group_joined:{group_id}")])
    rows += [
        [InlineKeyboardButton(text="👨‍🏫 Педагоги группы", callback_data=f"group_teachers:{group_id}")],
        [InlineKeyboardButton(text="💰 Биллинг ученикам", callback_data=f"group_billing:{group_id}")],
        [InlineKeyboardButton(
            text="♻️ Вернуть из архива" if archived else "📦 В архив",
            callback_data=f"group_arch:{'off' if archived else 'on'}:{group_id}",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_group_card(
    message: MaybeInaccessibleMessage | None, group_id: str,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    """Отрисовать карточку группы (edit_text). Используется и в callback, и после действий."""
    group = await group_repo.get_by_id(group_id)
    if not group:
        await message.edit_text("Группа не найдена", reply_markup=kb_back("admin:branches"))
        return
    branch = await branch_repo.get_by_id(group.branch_id)
    branch_name = branch.name if branch else group.branch_id

    teacher_ids = await teacher_group_repo.get_teachers_for_group(group_id)
    teachers_map = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
    teachers_list = ", ".join(teachers_map.get(tid, tid) for tid in teacher_ids) or "—"

    students = await group_members(student_repo, student_group_repo, group_id)

    text = (
        f"{'📦' if group.archived else '💃'} <b>{group.name}</b>\n"
        f"🏢 Филиал: {branch_name}\n"
        f"ID: {group.group_id}\n\n"
        f"👨‍🏫 Педагоги: {teachers_list}\n\n"
        f"👩‍🎓 Учеников: {len(students)}"
    )
    if group.archived:
        text += (
            "\n\n📦 <b>В архиве</b> — группа скрыта из списков записи занятий,"
            " счетов и добавления учеников. История сохранена."
        )
    await show_card(
        message, text,
        reply_markup=_kb_group_card(
            group_id, group.branch_id, students,
            subscription=group.billing_mode == GroupBillingMode.SUBSCRIPTION,
            archived=group.archived,
        ),
    )

