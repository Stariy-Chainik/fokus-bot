from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    GroupRepository,
    BranchRepository,
)
from bot.services import (
    StudentService,
)
from bot.states import AddStudentStates
from bot.keyboards.admin import (
    kb_confirm,
    kb_back,
)
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Добавление ученика ───────────────────────────────────────────────────────

@router.callback_query(F.data == "students:add")
async def cb_add_student_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddStudentStates.entering_name)
    await callback.message.edit_text(
        "<b>Добавление ученика</b>\nВведите Фамилию Имя ученика:",
        reply_markup=kb_back("admin:students"),
    )
    await callback.answer()


@router.message(AddStudentStates.entering_name)
async def add_student_name(
    message: Message, state: FSMContext, branch_repo: BranchRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Фамилия Имя не может быть пустым. Введите ещё раз:")
        return
    if len(name.split()) < 2:
        await message.answer(
            "Нужно указать и фамилию, и имя (например: <b>Иванова Мария</b>). Введите ещё раз:"
        )
        return
    await state.update_data(name=name)
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    if not branches:
        await state.clear()
        await message.answer(
            "Нет ни одного филиала. Создайте филиал и группу в «🏢 Филиалы и группы».",
            reply_markup=kb_back("admin:students"),
        )
        return
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"add_st_branch:{b.branch_id}")]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="admin:students")])
    await state.set_state(AddStudentStates.choosing_branch)
    await message.answer(
        f"<b>Новый ученик: {name}</b>\nВыберите филиал:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "add_st_pick_branch")
async def cb_add_student_pick_branch(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("name") or ""
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"add_st_branch:{b.branch_id}")]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="admin:students")])
    await state.set_state(AddStudentStates.choosing_branch)
    await callback.message.edit_text(
        f"<b>Новый ученик: {name}</b>\nВыберите филиал:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_st_branch:"), AddStudentStates.choosing_branch)
async def cb_add_student_branch(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    if not groups:
        await callback.answer(
            "В этом филиале нет групп. Создайте группу в «🏢 Филиалы и группы».",
            show_alert=True,
        )
        return
    await state.update_data(branch_id=branch_id)
    await state.set_state(AddStudentStates.choosing_group)
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"add_st_group:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="add_st_pick_branch")])
    await callback.message.edit_text(
        "<b>Выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_st_group:"), AddStudentStates.choosing_group)
async def cb_add_student_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await state.update_data(group_id=group_id)
    data = await state.get_data()
    await state.set_state(AddStudentStates.confirming)
    await callback.message.edit_text(
        f"<b>Добавить ученика «{data['name']}»?</b>\n(группа будет назначена)",
        reply_markup=kb_confirm("confirm_add_student", "admin:students"),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_add_student")
async def cb_confirm_add_student(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    try:
        created = await student_service.create_with_group(
            data["name"], data.get("group_id") or "",
        )
        group_info = ""
        if created.group:
            group_info = (
                f"\nГруппа: <b>{created.group.name}</b> (филиал «{created.branch_name}»)"
            )
        await callback.message.edit_text(
            f"<b>✅ Ученик добавлен</b>\n\n"
            f"Имя: <b>{created.student.name}</b>\n"
            f"ID: <code>{created.student.student_id}</code>"
            f"{group_info}",
            reply_markup=kb_back("admin:students"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления ученика: %s", exc)
        await callback.message.edit_text("Ошибка при добавлении ученика.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await callback.answer()


