from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from datetime import date

from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository,
)
from bot.services import PaymentService
from bot.states import (
    AddBranchStates, EditBranchNameStates,
    AddGroupStates, EditGroupNameStates,
)
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period

logger = logging.getLogger(__name__)
router = Router(name="admin_branches")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def _kb_branches_list(branches: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"branch_card:{b.branch_id}")]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="➕ Создать филиал", callback_data="branch:add")])
    if branches:
        rows.append([InlineKeyboardButton(text="✏️ Переименовать филиал", callback_data="branch:rename_pick")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_branch_card(branch_id: str, groups: list, has_groups: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"group_card:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="➕ Создать группу", callback_data=f"group:add:{branch_id}")])
    if has_groups:
        rows.append([InlineKeyboardButton(text="🗑 Удалить группу", callback_data=f"group:del_pick:{branch_id}")])
        rows.append([InlineKeyboardButton(text="✏️ Переименовать группу", callback_data=f"group:rename_pick:{branch_id}")])
    if not has_groups:
        rows.append([InlineKeyboardButton(text="🗑 Удалить филиал", callback_data=f"branch:del:{branch_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:branches")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_group_card(group_id: str, branch_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Педагоги группы", callback_data=f"group_teachers:{group_id}")],
        [InlineKeyboardButton(text="👩‍🎓 Ученики группы", callback_data=f"group_students:{group_id}")],
        [InlineKeyboardButton(text="📤 Разослать счета группе", callback_data=f"group_send_bills:{group_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")],
    ])


def _kb_group_teachers(group_id: str, teachers: list, assigned: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for t in teachers:
        mark = "✅ " if t.teacher_id in assigned else "☐ "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{t.name}", callback_data=f"gt_toggle:{group_id}:{t.teacher_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_group_students(group_id: str, students: list, assigned: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in students:
        mark = "✅ " if s.student_id in assigned else "☐ "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{s.name}", callback_data=f"gs_toggle:{group_id}:{s.student_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── Список филиалов ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:branches")
async def cb_branches_menu(
    callback: CallbackQuery, user: User | None, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    text = "<b>🏢 Филиалы и группы</b>\n\n"
    text += f"Всего филиалов: {len(branches)}" if branches else "Филиалов пока нет."
    await callback.message.edit_text(text, reply_markup=_kb_branches_list(branches))
    await callback.answer()


# ─── Создание филиала ────────────────────────────────────────────────────────

@router.callback_query(F.data == "branch:add")
async def cb_branch_add_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddBranchStates.entering_name)
    await callback.message.edit_text(
        "<b>Новый филиал</b>\nВведите название:",
        reply_markup=kb_back("admin:branches"),
    )
    await callback.answer()


@router.message(AddBranchStates.entering_name)
async def branch_add_name(
    message: Message, state: FSMContext, branch_repo: BranchRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    await state.clear()
    branch = await branch_repo.add(name)
    await message.answer(
        f"✅ Филиал «{branch.name}» создан.", reply_markup=kb_back("admin:branches"),
    )


# ─── Карточка филиала ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("branch_card:"))
async def cb_branch_card(
    callback: CallbackQuery, user: User | None,
    branch_repo: BranchRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    branch = await branch_repo.get_by_id(branch_id)
    if not branch:
        await callback.answer("Филиал не найден", show_alert=True)
        return
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    text = (
        f"🏢 <b>{branch.name}</b>\n"
        f"ID: {branch.branch_id}\n\n"
        f"Групп: {len(groups)}"
    )
    await callback.message.edit_text(
        text, reply_markup=_kb_branch_card(branch_id, groups, has_groups=bool(groups)),
    )
    await callback.answer()


# ─── Переименование филиала ──────────────────────────────────────────────────

@router.callback_query(F.data == "branch:rename_pick")
async def cb_branch_rename_pick(
    callback: CallbackQuery, user: User | None, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    buttons = [
        [InlineKeyboardButton(text=b.name, callback_data=f"branch:edit_name:{b.branch_id}")]
        for b in branches
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:branches")])
    await callback.message.edit_text(
        "<b>Выберите филиал для переименования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("branch:edit_name:"))
async def cb_branch_edit_name_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 2)[2]
    await state.set_state(EditBranchNameStates.entering_name)
    await state.update_data(branch_id=branch_id)
    await callback.message.edit_text(
        "<b>Новое название филиала:</b>",
        reply_markup=kb_back(f"branch_card:{branch_id}"),
    )
    await callback.answer()


@router.message(EditBranchNameStates.entering_name)
async def branch_edit_name_save(
    message: Message, state: FSMContext, branch_repo: BranchRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    branch_id = data["branch_id"]
    ok = await branch_repo.update_name(branch_id, name)
    text = "✅ Название обновлено." if ok else "Филиал не найден."
    await message.answer(text, reply_markup=kb_back(f"branch_card:{branch_id}"))


# ─── Удаление филиала ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("branch:del:"))
async def cb_branch_del_confirm(
    callback: CallbackQuery, user: User | None,
    branch_repo: BranchRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 2)[2]
    branch = await branch_repo.get_by_id(branch_id)
    if not branch:
        await callback.answer("Филиал не найден", show_alert=True)
        return
    groups = await group_repo.get_by_branch(branch_id)
    if groups:
        await callback.answer("Сначала удалите все группы филиала.", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить филиал «{branch.name}»?</b>",
        reply_markup=kb_confirm(
            f"confirm_del_branch:{branch_id}", f"branch_card:{branch_id}",
            confirm_text="🗑 Удалить",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_branch:"))
async def cb_branch_del_do(
    callback: CallbackQuery, user: User | None, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    ok = await branch_repo.delete(branch_id)
    text = "Филиал удалён." if ok else "Филиал не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:branches"))
    await callback.answer()


# ─── Создание группы ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group:add:"))
async def cb_group_add_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 2)[2]
    await state.set_state(AddGroupStates.entering_name)
    await state.update_data(branch_id=branch_id)
    await callback.message.edit_text(
        "<b>Новая группа</b>\nВведите название (например «Пн/Ср 18:00 Начинающие»):",
        reply_markup=kb_back(f"branch_card:{branch_id}"),
    )
    await callback.answer()


@router.message(AddGroupStates.entering_name)
async def group_add_name(
    message: Message, state: FSMContext, group_repo: GroupRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    branch_id = data["branch_id"]
    group = await group_repo.add(branch_id, name)
    await message.answer(
        f"✅ Группа «{group.name}» создана.",
        reply_markup=kb_back(f"branch_card:{branch_id}"),
    )


# ─── Карточка группы ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_card:"))
async def cb_group_card(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    branch = await branch_repo.get_by_id(group.branch_id)
    branch_name = branch.name if branch else group.branch_id

    teacher_ids = await teacher_group_repo.get_teachers_for_group(group_id)
    teachers_map = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
    teachers_list = ", ".join(teachers_map.get(tid, tid) for tid in teacher_ids) or "—"

    students = [s for s in await student_repo.get_all() if s.group_id == group_id]
    students.sort(key=lambda s: s.name)
    students_text = "\n".join(f"  • {s.name}" for s in students) or "  —"

    text = (
        f"💃 <b>{group.name}</b>\n"
        f"🏢 Филиал: {branch_name}\n"
        f"ID: {group.group_id}\n\n"
        f"👨‍🏫 Педагоги: {teachers_list}\n\n"
        f"👩‍🎓 Ученики ({len(students)}):\n{students_text}"
    )
    await callback.message.edit_text(text, reply_markup=_kb_group_card(group_id, group.branch_id))
    await callback.answer()


# ─── Переименование группы ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group:rename_pick:"))
async def cb_group_rename_pick(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 2)[2]
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    buttons = [
        [InlineKeyboardButton(text=g.name, callback_data=f"group:edit_name:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")])
    await callback.message.edit_text(
        "<b>Выберите группу для переименования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group:edit_name:"))
async def cb_group_edit_name_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 2)[2]
    await state.set_state(EditGroupNameStates.entering_name)
    await state.update_data(group_id=group_id)
    await callback.message.edit_text(
        "<b>Новое название группы:</b>",
        reply_markup=kb_back(f"group_card:{group_id}"),
    )
    await callback.answer()


@router.message(EditGroupNameStates.entering_name)
async def group_edit_name_save(
    message: Message, state: FSMContext, group_repo: GroupRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    group_id = data["group_id"]
    ok = await group_repo.update_name(group_id, name)
    text = "✅ Название обновлено." if ok else "Группа не найдена."
    await message.answer(text, reply_markup=kb_back(f"group_card:{group_id}"))


# ─── Удаление группы ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group:del_pick:"))
async def cb_group_del_pick(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 2)[2]
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {g.name}", callback_data=f"group:del:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")])
    await callback.message.edit_text(
        "<b>Выберите группу для удаления:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group:del:"))
async def cb_group_del_confirm(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 2)[2]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить группу «{group.name}»?</b>\n"
        "Связи педагогов с группой будут удалены, у учеников обнулится group_id.",
        reply_markup=kb_confirm(
            f"confirm_del_group:{group_id}", f"branch_card:{group.branch_id}",
            confirm_text="🗑 Удалить",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_group:"))
async def cb_group_del_do(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, teacher_group_repo: TeacherGroupRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    branch_id = group.branch_id
    # Обнуляем group_id у учеников этой группы
    for s in await student_repo.get_all():
        if s.group_id == group_id:
            await student_repo.update_group(s.student_id, "")
    # Удаляем связи педагог↔группа
    await teacher_group_repo.remove_all_for_group(group_id)
    # Удаляем саму группу
    await group_repo.delete(group_id)
    await callback.message.edit_text(
        "Группа удалена.", reply_markup=kb_back(f"branch_card:{branch_id}"),
    )
    await callback.answer()


# ─── Педагоги группы (чекбоксы) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_teachers:"))
async def cb_group_teachers(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, teacher_group_repo: TeacherGroupRepository,
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
    teachers = sorted(await teacher_repo.get_all(), key=lambda t: t.name)
    assigned = set(await teacher_group_repo.get_teachers_for_group(group_id))
    await callback.message.edit_text(
        f"<b>Педагоги группы «{group.name}»</b>\nОтметьте педагогов:",
        reply_markup=_kb_group_teachers(group_id, teachers, assigned),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gt_toggle:"))
async def cb_gt_toggle(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, teacher_id = callback.data.split(":", 2)
    if await teacher_group_repo.exists(teacher_id, group_id):
        await teacher_group_repo.remove(teacher_id, group_id)
    else:
        await teacher_group_repo.add(teacher_id, group_id)
    teachers = sorted(await teacher_repo.get_all(), key=lambda t: t.name)
    assigned = set(await teacher_group_repo.get_teachers_for_group(group_id))
    await callback.message.edit_reply_markup(
        reply_markup=_kb_group_teachers(group_id, teachers, assigned),
    )
    await callback.answer()


# ─── Ученики группы (чекбоксы) ───────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_students:"))
async def cb_group_students(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    # Показываем: учеников этой группы + учеников без группы (которых можно добавить).
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    assigned = {s.student_id for s in all_students if s.group_id == group_id}
    candidates = [s for s in all_students if s.group_id == group_id or not s.group_id]
    await callback.message.edit_text(
        f"<b>Ученики группы «{group.name}»</b>\n"
        "✅ — в группе. Доступны также ученики без группы.\n"
        "Ученики из других групп здесь не видны (смена группы — вручную в Google Sheets).",
        reply_markup=_kb_group_students(group_id, candidates, assigned),
    )
    await callback.answer()


_group_send_in_progress: set[str] = set()


@router.callback_query(F.data.startswith("group_send_bills:"))
async def cb_group_send_bills(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    teacher_repo: TeacherRepository, payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    period_month = date.today().strftime("%Y-%m")
    lock_key = f"{group_id}:{period_month}"
    if lock_key in _group_send_in_progress:
        await callback.answer("Рассылка уже выполняется", show_alert=True)
        return
    _group_send_in_progress.add(lock_key)

    try:
        students = [s for s in await student_repo.get_all() if s.group_id == group_id]
        if not students:
            await callback.answer("В группе нет учеников", show_alert=True)
            return

        # Собираем педагогов из всех счетов учеников группы
        all_teacher_ids: set[str] = set()
        per_student_bills: dict[str, dict] = {}
        for s in students:
            bills = await payment_service.compute_bills_for_student_period(
                s.student_id, period_month,
            )
            per_student_bills[s.student_id] = bills
            all_teacher_ids.update(bills.keys())

        if not all_teacher_ids:
            await callback.answer(
                f"За {display_period(period_month)} нет индивидуальных занятий учеников группы.",
                show_alert=True,
            )
            return

        not_submitted = await payment_service.teachers_not_submitted(
            list(all_teacher_ids), period_month,
        )
        if not_submitted:
            names = []
            for tid in not_submitted:
                t = await teacher_repo.get_by_id(tid)
                names.append(t.name if t else tid)
            await callback.answer(
                "Период не сдан педагогами:\n" + "\n".join(names),
                show_alert=True,
            )
            return

        # Все сдали — создаём счета и отправляем (заглушка)
        total_invoices = 0
        for s in students:
            if not per_student_bills[s.student_id]:
                continue
            invoices = await payment_service.get_or_create_invoices_for_student_period(
                s, period_month,
            )
            total_invoices += len(invoices)
        await callback.answer(
            f"Счёта группы «{group.name}» за {display_period(period_month)} разосланы родителям (заглушка). "
            f"Учеников: {len(students)}, счетов: {total_invoices}.",
            show_alert=True,
        )
    finally:
        _group_send_in_progress.discard(lock_key)


@router.callback_query(F.data.startswith("gs_toggle:"))
async def cb_gs_toggle(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    if student.group_id == group_id:
        # Снятие: обнуляем group_id
        await student_repo.update_group(student_id, "")
    elif not student.group_id:
        # Добавление в группу
        await student_repo.update_group(student_id, group_id)
    else:
        # У ученика другая группа — не трогаем через UI
        await callback.answer("Ученик уже в другой группе — меняйте вручную в Sheets.", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    assigned = {s.student_id for s in all_students if s.group_id == group_id}
    candidates = [s for s in all_students if s.group_id == group_id or not s.group_id]
    await callback.message.edit_reply_markup(
        reply_markup=_kb_group_students(group_id, candidates, assigned),
    )
    await callback.answer()
