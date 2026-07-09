from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from datetime import date

from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository, StudentGroupRepository,
)
from bot.services import PaymentService
from bot.states import (
    AddBranchStates, EditBranchNameStates,
    AddGroupStates, EditGroupNameStates,
    GroupBillingStates, GroupAddStudentStates,
)
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period
from bot.utils.locks import InProgressGuard
from bot.handlers.common import show_card

logger = logging.getLogger(__name__)
router = Router(name="admin_branches")


from bot.handlers.access import is_admin as _is_admin


# ─── Общий рендер карточки группы ─────────────────────────────────────────────

def _kb_group_card(group_id: str, branch_id: str, students: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"student_card:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить ученика", callback_data=f"group_add_student:{group_id}")])
    if students:
        rows.append([InlineKeyboardButton(text="➖ Убрать ученика", callback_data=f"group_rm_student:{group_id}")])
    rows += [
        [InlineKeyboardButton(text="👨‍🏫 Педагоги группы", callback_data=f"group_teachers:{group_id}")],
        [InlineKeyboardButton(text="💰 Биллинг ученикам", callback_data=f"group_billing:{group_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_group_card(
    message: Message, group_id: str,
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

    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = [s for s in await student_repo.get_all() if s.student_id in member_ids]
    students.sort(key=lambda s: s.name)

    text = (
        f"💃 <b>{group.name}</b>\n"
        f"🏢 Филиал: {branch_name}\n"
        f"ID: {group.group_id}\n\n"
        f"👨‍🏫 Педагоги: {teachers_list}\n\n"
        f"👩‍🎓 Учеников: {len(students)}"
    )
    await show_card(
        message, text,
        reply_markup=_kb_group_card(group_id, group.branch_id, students),
    )

