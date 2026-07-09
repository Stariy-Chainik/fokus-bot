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
from ._base import _tid
from .flows import _collect_pairs, _collect_soloists
from .finalize import _finalize
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


# ─── Pair: мульти-выбор пар ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pair_toggle:"), RecordLessonStates.choosing_pair)
async def cb_pair_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    await state.update_data(selected_ids=selected)

    pairs = await _collect_pairs(_tid(user, data), visibility)
    await callback.message.edit_reply_markup(
        reply_markup=kb_pair_multi_select(
            pairs, set(selected),
            back_cb="lesson_back:duration",
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pair_confirm", RecordLessonStates.choosing_pair)
async def cb_pair_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService,
) -> None:
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if not selected:
        await callback.answer("Ни одна пара не отмечена", show_alert=True)
        return
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service)


# ─── Соло 2 и больше: одно занятие, счёт делится поровну ──────────────────

@router.callback_query(F.data.startswith("pso_toggle:"), RecordLessonStates.picking_pair_soloists)
async def cb_pso_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    sid = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if sid in selected:
        selected.remove(sid)
    elif len(selected) >= 4:
        await callback.answer("Можно выбрать не больше 4 солистов", show_alert=True)
        return
    else:
        selected.append(sid)
    await state.update_data(selected_ids=selected)
    gid = data.get("selected_group_id") or ""
    back_cb = "lesson_back:group" if gid else "lesson_back:duration"
    if gid:
        members = await visibility.students_in_group_for_teacher(_tid(user, data), gid)
        soloists = sorted(members, key=lambda s: s.name)
    else:
        soloists = await _collect_soloists(_tid(user, data), visibility)
    await callback.message.edit_reply_markup(
        reply_markup=kb_pair_from_soloists(soloists, set(selected), back_cb=back_cb),
    )
    await callback.answer()


@router.callback_query(F.data == "pso_confirm", RecordLessonStates.picking_pair_soloists)
async def cb_pso_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService,
) -> None:
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if not (2 <= len(selected) <= 4):
        await callback.answer("Нужно выбрать от 2 до 4 солистов", show_alert=True)
        return
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service)


