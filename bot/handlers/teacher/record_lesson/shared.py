from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode, StudentGroupTier
from bot.models.enums import LessonType
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, UserRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_lesson_type_after_save, kb_duration, kb_teacher_menu,
    kb_attendance_yes_no, kb_pair_multi_select, kb_pair_from_soloists,
    kb_multi_select, kb_group_roster_per_visit,
    kb_other_groups_picker, kb_other_group_students,
    kb_group_branch_picker, kb_group_picker, kb_shared_group_picker,
)
from bot.keyboards.admin import kb_admin_menu
from bot.utils import build_group_attendees_csv
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display
from bot.utils.locks import InProgressGuard

from ._base import router
from ._base import _tid, _header
from .flows import _show_shared_group_picker
from .finalize import _finalize
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("shared_group:"), RecordLessonStates.choosing_shared_group)
async def cb_shared_group_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, group_repo: GroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    data = await state.get_data()
    members = await visibility.students_in_group_for_teacher(_tid(user, data), group_id)
    members = sorted(members, key=lambda s: s.name)
    if not members:
        await callback.answer(f"В группе «{group.name}» нет учеников", show_alert=True)
        return
    member_ids = {s.student_id for s in members}
    all_selected = set(data.get("selected_ids") or [])
    already_selected = all_selected & member_ids
    await state.update_data(selected_group_id=group_id)
    await state.set_state(RecordLessonStates.picking_shared_in_group)
    finish = f"✅ Подтвердить занятие ({len(all_selected)})" if len(all_selected) >= 2 else None
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметьте учеников (max 4 всего).",
        reply_markup=kb_pair_from_soloists(
            members, already_selected,
            back_cb="shared_back_to_groups",
            done_label="✓ К выбору групп",
            done_cb="pso_confirm",
            finish_label=finish,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pso_toggle:"), RecordLessonStates.picking_shared_in_group)
async def cb_shared_student_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    sid = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected: list = list(data.get("selected_ids") or [])
    by_group: dict = dict(data.get("selected_ids_by_group") or {})
    group_id = data.get("selected_group_id") or ""

    if sid in selected:
        selected.remove(sid)
        if group_id in by_group and sid in by_group[group_id]:
            by_group[group_id].remove(sid)
    else:
        if len(selected) >= 4:
            await callback.answer("Максимум 4 ученика", show_alert=True)
            return
        selected.append(sid)
        by_group.setdefault(group_id, [])
        if sid not in by_group[group_id]:
            by_group[group_id].append(sid)

    await state.update_data(selected_ids=selected, selected_ids_by_group=by_group)

    members = await visibility.students_in_group_for_teacher(_tid(user, data), group_id)
    members = sorted(members, key=lambda s: s.name)
    finish = f"✅ Подтвердить занятие ({len(selected)})" if len(selected) >= 2 else None
    await callback.message.edit_reply_markup(
        reply_markup=kb_pair_from_soloists(
            members, set(selected),
            back_cb="shared_back_to_groups",
            done_label="✓ К выбору групп",
            done_cb="pso_confirm",
            finish_label=finish,
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pso_confirm", RecordLessonStates.picking_shared_in_group)
async def cb_shared_pso_done(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_shared_group_picker(callback, state, user, teacher_group_repo, group_repo)
    await callback.answer()


@router.callback_query(F.data == "shared_back_to_groups")
async def cb_shared_back_to_groups(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_shared_group_picker(callback, state, user, teacher_group_repo, group_repo)
    await callback.answer()


@router.callback_query(F.data == "shared_confirm", RecordLessonStates.picking_shared_in_group)
async def cb_shared_confirm_from_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService,
) -> None:
    data = await state.get_data()
    selected = list(data.get("selected_ids") or [])
    if not (2 <= len(selected) <= 4):
        await callback.answer("Нужно от 2 до 4 учеников", show_alert=True)
        return
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service)


@router.callback_query(F.data == "shared_confirm", RecordLessonStates.choosing_shared_group)
async def cb_shared_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService,
) -> None:
    data = await state.get_data()
    selected = list(data.get("selected_ids") or [])
    if not (2 <= len(selected) <= 4):
        await callback.answer("Нужно от 2 до 4 учеников", show_alert=True)
        return
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service)

