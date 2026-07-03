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
    await show_card(
        callback, text,
        reply_markup=_kb_branch_card(branch_id, groups, has_groups=bool(groups)),
    )


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
        reply_markup=kb_back("branch:rename_pick"),
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


