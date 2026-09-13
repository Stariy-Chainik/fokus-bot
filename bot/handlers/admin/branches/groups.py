from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User

from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository, StudentGroupRepository,
)
from bot.states import (
    AddGroupStates, EditGroupNameStates,
)
from bot.keyboards.admin import kb_back, kb_confirm
from bot.handlers.filters import AdminOnly

from bot.utils.callbacks import (
    ConfirmDelGroupCb,
    GroupAddCb,
    GroupArchiveCb,
    GroupCardCb,
    GroupDelCb,
    GroupDelPickCb,
    GroupEditNameCb,
    GroupRenamePickCb,
    GroupTeacherToggleCb,
    GroupTeachersCb,
)
from ._base import router
from ._base import _render_group_card

logger = logging.getLogger(__name__)


def _kb_group_teachers(group_id: str, teachers: list, assigned: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for t in teachers:
        mark = "✅ " if t.teacher_id in assigned else "☐ "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{t.name}", callback_data=f"gt_toggle:{group_id}:{t.teacher_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



# ─── Создание группы ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group:add:"), AdminOnly())
async def cb_group_add_start(
    callback: CallbackQuery, user: User, state: FSMContext,
) -> None:
    branch_id = GroupAddCb.unpack(callback.data).branch_id
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


@router.callback_query(F.data.startswith("group_card:"), AdminOnly())
async def cb_group_card(
    callback: CallbackQuery, user: User,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    group_id = GroupCardCb.unpack(callback.data).group_id
    await _render_group_card(
        callback.message, group_id,
        group_repo, branch_repo, teacher_repo, student_repo,
        teacher_group_repo, student_group_repo,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group_arch:"), AdminOnly())
async def cb_group_archive(
    callback: CallbackQuery, user: User,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    """В архив / из архива. Строка группы остаётся — история занятий и оплат цела."""
    cb = GroupArchiveCb.unpack(callback.data)
    action, group_id = cb.action, cb.group_id
    archived = action == "on"
    ok = await group_repo.set_archived(group_id, archived)
    if ok:
        logger.info(
            "Группа %s %s (админ %s)",
            group_id, "в архиве" if archived else "возвращена из архива", user.tg_id,
        )
    await _render_group_card(
        callback.message, group_id,
        group_repo, branch_repo, teacher_repo, student_repo,
        teacher_group_repo, student_group_repo,
    )
    await callback.answer("📦 В архиве" if archived else "♻️ Вернули из архива")


# ─── Переименование группы ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("group:rename_pick:"), AdminOnly())
async def cb_group_rename_pick(
    callback: CallbackQuery, user: User, group_repo: GroupRepository,
) -> None:
    branch_id = GroupRenamePickCb.unpack(callback.data).branch_id
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    buttons = [
        [InlineKeyboardButton(text=g.name, callback_data=f"group:edit_name:{branch_id}:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"branch_card:{branch_id}")])
    await callback.message.edit_text(
        "<b>Выберите группу для переименования:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group:edit_name:"), AdminOnly())
async def cb_group_edit_name_start(
    callback: CallbackQuery, user: User, state: FSMContext,
) -> None:
    cb = GroupEditNameCb.unpack(callback.data)
    branch_id, group_id = cb.branch_id, cb.group_id
    await state.set_state(EditGroupNameStates.entering_name)
    await state.update_data(group_id=group_id, branch_id=branch_id)
    await callback.message.edit_text(
        "<b>Новое название группы:</b>",
        reply_markup=kb_back(f"group:rename_pick:{branch_id}"),
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

@router.callback_query(F.data.startswith("group:del_pick:"), AdminOnly())
async def cb_group_del_pick(
    callback: CallbackQuery, user: User, group_repo: GroupRepository,
) -> None:
    branch_id = GroupDelPickCb.unpack(callback.data).branch_id
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


@router.callback_query(F.data.startswith("group:del:"), AdminOnly())
async def cb_group_del_confirm(
    callback: CallbackQuery, user: User, group_repo: GroupRepository,
) -> None:
    group_id = GroupDelCb.unpack(callback.data).group_id
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить группу «{group.name}»?</b>\n"
        "Связи педагогов с группой будут удалены, ученики будут изъяты из этой группы "
        "(в остальных своих группах они остаются).",
        reply_markup=kb_confirm(
            f"confirm_del_group:{group_id}", f"branch_card:{group.branch_id}",
            confirm_text="🗑 Удалить",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_group:"), AdminOnly())
async def cb_group_del_do(
    callback: CallbackQuery, user: User,
    group_repo: GroupRepository, teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    group_id = ConfirmDelGroupCb.unpack(callback.data).group_id
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    branch_id = group.branch_id
    # Удаляем членство учеников в этой группе
    await student_group_repo.remove_all_for_group(group_id)
    # Удаляем связи педагог↔группа
    await teacher_group_repo.remove_all_for_group(group_id)
    # Удаляем саму группу
    await group_repo.delete(group_id)
    await callback.message.edit_text(
        "Группа удалена.", reply_markup=kb_back(f"branch_card:{branch_id}"),
    )
    await callback.answer()


# ─── Педагоги группы (чекбоксы) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_teachers:"), AdminOnly())
async def cb_group_teachers(
    callback: CallbackQuery, user: User,
    teacher_repo: TeacherRepository, teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
) -> None:
    group_id = GroupTeachersCb.unpack(callback.data).group_id
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


@router.callback_query(F.data.startswith("gt_toggle:"), AdminOnly())
async def cb_gt_toggle(
    callback: CallbackQuery, user: User,
    teacher_repo: TeacherRepository, teacher_group_repo: TeacherGroupRepository,
) -> None:
    cb = GroupTeacherToggleCb.unpack(callback.data)
    group_id, teacher_id = cb.group_id, cb.teacher_id
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


