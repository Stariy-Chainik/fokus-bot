from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User

from bot.repositories import (
    TeacherRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
)
from bot.states import EditTeacherRatesStates
from bot.keyboards.admin import kb_teacher_card, kb_rate_select, kb_back
from bot.handlers.common import show_card




from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


def _kb_teacher_groups_edit(teacher_id: str, groups: list, branches: dict, draft: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for g in groups:
        mark = "✅ " if g.group_id in draft else "☐ "
        bname = branches.get(g.branch_id, "—")
        rows.append([InlineKeyboardButton(
            text=f"{mark}{bname} / {g.name}",
            callback_data=f"teg_toggle:{teacher_id}:{g.group_id}",
        )])
    rows.append([
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"teg_confirm:{teacher_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"teg_cancel:{teacher_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_teacher_card(
    callback: CallbackQuery,
    teacher_id: str,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.message.edit_text("Педагог не найден", reply_markup=kb_back("teachers:list"))
        return
    tg_info = f"@tg_id: {teacher.tg_id}" if teacher.tg_id else "Telegram не привязан"
    gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    groups = [g for g in await group_repo.get_all() if g.group_id in gids]
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups.sort(key=lambda g: (branches.get(g.branch_id, ""), g.name))
    groups_block = (
        "\n".join(f"  • {branches.get(g.branch_id, '—')} / {g.name}" for g in groups)
        if groups else "  —"
    )
    text = (
        f"👨‍🏫 <b>{teacher.name}</b>\n"
        f"ID: {teacher.teacher_id}\n"
        f"{tg_info}\n\n"
        f"📊 Ставки (руб. за 45 мин):\n"
        f"  Групповое: <b>{teacher.rate_group}</b>\n"
        f"  Инд. педагогу: <b>{teacher.rate_for_teacher}</b>\n"
        f"  Инд. ученику: <b>{teacher.rate_for_student}</b>\n\n"
        f"🏢 Группы:\n{groups_block}"
    )
    await show_card(callback, text, reply_markup=kb_teacher_card(teacher_id))


@router.callback_query(F.data.startswith("t_edit_groups:"))
async def cb_t_edit_groups(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups = sorted(
        await group_repo.get_all(),
        key=lambda g: (branches.get(g.branch_id, ""), g.name),
    )
    assigned = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    await state.clear()
    await state.update_data(
        teg_teacher_id=teacher_id,
        teg_original=list(assigned),
        teg_draft=list(assigned),
    )
    await callback.message.edit_text(
        f"<b>Группы педагога «{teacher.name}»</b>\n"
        "Тап по группе — переключает галочку.\n"
        "«✅ Подтвердить» — сохраняет. «❌ Отмена» — откатывает.",
        reply_markup=_kb_teacher_groups_edit(teacher_id, groups, branches, assigned),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teg_toggle:"))
async def cb_teg_toggle(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, group_id = callback.data.split(":", 2)
    data = await state.get_data()
    draft = set(data.get("teg_draft", []))
    if group_id in draft:
        draft.discard(group_id)
    else:
        draft.add(group_id)
    await state.update_data(teg_draft=list(draft))
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups = sorted(
        await group_repo.get_all(),
        key=lambda g: (branches.get(g.branch_id, ""), g.name),
    )
    await callback.message.edit_reply_markup(
        reply_markup=_kb_teacher_groups_edit(teacher_id, groups, branches, draft),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teg_confirm:"))
async def cb_teg_confirm(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    original = set(data.get("teg_original", []))
    draft = set(data.get("teg_draft", []))
    to_add = draft - original
    to_remove = original - draft
    for gid in to_add:
        await teacher_group_repo.add(teacher_id, gid)
    for gid in to_remove:
        await teacher_group_repo.remove(teacher_id, gid)
    await state.clear()
    changed = len(to_add) + len(to_remove)
    await callback.answer("Сохранено" if changed else "Без изменений")
    await _render_teacher_card(
        callback, teacher_id, teacher_repo, teacher_group_repo, group_repo, branch_repo,
    )


@router.callback_query(F.data.startswith("teg_cancel:"))
async def cb_teg_cancel(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.clear()
    await callback.answer("Отменено")
    await _render_teacher_card(
        callback, teacher_id, teacher_repo, teacher_group_repo, group_repo, branch_repo,
    )


@router.callback_query(F.data.startswith("card_edit_rates:"))
async def cb_card_edit_rates(
    callback: CallbackQuery, user: User | None, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    await state.update_data(
        teacher_id=teacher_id,
        rate_group=teacher.rate_group,
        rate_for_teacher=teacher.rate_for_teacher,
        rate_for_student=teacher.rate_for_student,
    )
    await state.set_state(EditTeacherRatesStates.choosing_rate)
    await callback.message.edit_text(
        f"Педагог: <b>{teacher.name}</b>\n\nКакую ставку изменить?",
        reply_markup=kb_rate_select(
            teacher_id, teacher.rate_group, teacher.rate_for_teacher, teacher.rate_for_student
        ),
    )
    await callback.answer()

