from __future__ import annotations
import logging

from aiogram import F
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
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)
from ._base import _render_group_card


# ─── Добавление ученика в группу (поиск или создание) ───────────────────────

def _normalize(q: str) -> str:
    return " ".join((q or "").strip().split()).lower()


def _kb_group_add_results(
    group_id: str, found: list, member_ids: set[str], query: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in found[:20]:
        mark = " ✓" if s.student_id in member_ids else ""
        rows.append([InlineKeyboardButton(
            text=f"{s.name}{mark}",
            callback_data=f"grp_add_pick:{group_id}:{s.student_id}",
        )])
    # Кнопка «создать нового» — только если ввод похож на ФИО (два слова).
    parts = (query or "").strip().split()
    if len(parts) == 2:
        rows.append([InlineKeyboardButton(
            text=f"✨ Создать нового «{' '.join(parts)}»",
            callback_data=f"grp_add_new:{group_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("group_add_student:"))
async def cb_group_add_student(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await state.set_state(GroupAddStudentStates.searching)
    await state.update_data(group_id=group_id)
    await callback.message.edit_text(
        f"<b>➕ Добавить ученика в «{group.name}»</b>\n\n"
        "Введите <b>Фамилию Имя</b> (или часть). Бот найдёт совпадения.\n"
        "Если ученика нет — предложит создать нового.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"group_card:{group_id}")],
        ]),
    )
    await callback.answer()


@router.message(GroupAddStudentStates.searching)
async def msg_group_add_search(
    message: Message, state: FSMContext, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        return
    data = await state.get_data()
    group_id = str(data.get("group_id") or "")
    if not group_id:
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("❗ Введите ФИО или часть.")
        return
    await state.update_data(query=query)

    q_norm = _normalize(query)
    all_students = await student_repo.get_all()
    found = sorted(
        [s for s in all_students if q_norm in s.name.lower()],
        key=lambda s: s.name,
    )
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    group = await group_repo.get_by_id(group_id)
    header = f"Группа: <b>{group.name if group else group_id}</b>"

    if not found:
        parts = query.split()
        lines = [header, "", f"По запросу «{query}» ничего не найдено."]
        if len(parts) == 2:
            lines.append("")
            lines.append("Можно создать нового ученика с этим ФИО.")
        else:
            lines.append("")
            lines.append("Для создания нового введите ровно <b>Фамилию и Имя</b>.")
        await message.answer(
            "\n".join(lines),
            reply_markup=_kb_group_add_results(group_id, [], member_ids, query),
        )
        return

    lines = [header, "", f"Найдено: <b>{len(found)}</b>. ✓ — уже в этой группе."]
    await message.answer(
        "\n".join(lines),
        reply_markup=_kb_group_add_results(group_id, found, member_ids, query),
    )


@router.callback_query(F.data.startswith("grp_add_pick:"), GroupAddStudentStates.searching)
async def cb_group_add_pick_existing(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_group_repo: StudentGroupRepository,
    student_repo: StudentRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    if await student_group_repo.exists(student_id, group_id):
        await callback.answer(f"{student.name} уже в этой группе", show_alert=True)
    else:
        await student_group_repo.add(student_id, group_id)
        await callback.answer(f"✅ {student.name} добавлен(а)")
    await state.clear()
    await _render_group_card(
        callback.message, group_id,
        group_repo, branch_repo, teacher_repo, student_repo,
        teacher_group_repo, student_group_repo,
    )


@router.callback_query(F.data.startswith("grp_add_new:"), GroupAddStudentStates.searching)
async def cb_group_add_new(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    query = str(data.get("query") or "").strip()
    parts = query.split()
    if len(parts) != 2:
        await callback.answer("Нужно ровно Фамилию и Имя", show_alert=True)
        return
    name = " ".join(parts)
    student = await student_repo.add(name=name)
    await student_group_repo.add(student.student_id, group_id)
    await state.clear()
    await callback.answer(f"✅ Создан: {student.name}")
    await _render_group_card(
        callback.message, group_id,
        group_repo, branch_repo, teacher_repo, student_repo,
        teacher_group_repo, student_group_repo,
    )


# ─── Удаление ученика из группы ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_rm_student:"))
async def cb_group_rm_student(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not members:
        await callback.answer("В группе нет учеников", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=f"➖ {s.name}", callback_data=f"grp_rm_do:{group_id}:{s.student_id}")]
        for s in members
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"group_card:{group_id}")])
    await callback.message.edit_text(
        f"<b>➖ Убрать из «{group.name}»</b>\n\nВыберите ученика — он будет убран только из этой группы. "
        f"В остальных своих группах останется.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("grp_rm_do:"))
async def cb_group_rm_do(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    removed = await student_group_repo.remove(student_id, group_id)
    if removed and student:
        await callback.answer(f"✅ {student.name} убран(а) из группы")
    else:
        await callback.answer("Не удалось убрать (возможно, уже убран)")
    await _render_group_card(
        callback.message, group_id,
        group_repo, branch_repo, teacher_repo, student_repo,
        teacher_group_repo, student_group_repo,
    )
