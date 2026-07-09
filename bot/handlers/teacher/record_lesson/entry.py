from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models import User
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_duration, kb_teacher_menu, kb_attendance_yes_no,
)
from bot.keyboards.admin import kb_admin_menu

from ._base import router
from ._base import _date_picker_kb, _header
from .flows import _start_group_flow, _show_pair_list
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data == "teacher:record_lesson")
async def cb_record_lesson_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(RecordLessonStates.choosing_date)
    await callback.message.edit_text(
        "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:cancel_lesson")
async def cb_cancel_lesson(callback: CallbackQuery, state: FSMContext, user: User | None) -> None:
    data = await state.get_data()
    is_proxy = bool(data.get("proxy_teacher_id"))
    await state.clear()
    if user and user.is_admin and (is_proxy or not user.teacher_id):
        await callback.message.edit_text("Отменено.", reply_markup=kb_admin_menu())
    else:
        can_switch = bool(user and user.is_admin and user.teacher_id)
        await callback.message.edit_text(
            "Отменено.", reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id if user else None),
        )
    await callback.answer()


# ─── Назад на шаг ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_back:"))
async def cb_lesson_back(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    target = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if target == "date":
        await state.set_state(RecordLessonStates.choosing_date)
        await callback.message.edit_text(
            "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
        )

    elif target == "kind":
        # очистим данные ниже по воронке
        await state.update_data(kind=None, duration_min=None, selected_ids=[])
        await state.set_state(RecordLessonStates.choosing_kind)
        data = await state.get_data()
        await callback.message.edit_text(
            f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
        )

    elif target == "duration":
        await state.update_data(selected_ids=[])
        await state.set_state(RecordLessonStates.choosing_duration)
        data = await state.get_data()
        await callback.message.edit_text(
            f"{_header(data)}Выберите длительность:",
            reply_markup=kb_duration(back_cb="lesson_back:kind"),
        )

    elif target == "attendance":
        # Возврат к вопросу «отметить присутствующих?» из roster.
        gid = data.get("selected_group_id") or ""
        group = await group_repo.get_by_id(gid) if gid else None
        gname = group.name if group else ""
        await state.set_state(RecordLessonStates.asking_attendance)
        await state.update_data(selected_ids=[])
        prefix = f"Группа: <b>{gname}</b>\n" if gname else ""
        await callback.message.edit_text(
            f"{_header(data)}{prefix}Отметить присутствующих?",
            reply_markup=kb_attendance_yes_no(),
        )

    elif target == "pair":
        await state.update_data(
            selected_group_id=None, selected_branch_id=None,
            selected_ids=[], group_auto=False, pair_from_soloists=False,
        )
        await _show_pair_list(callback, state, user, visibility)

    elif target == "group":
        # Возврат к пикеру группы (или филиала) из attendance yes/no / pso roster.
        if data.get("group_auto"):
            await state.update_data(
                selected_group_id=None, selected_ids=[],
                group_auto=False, pair_from_soloists=False,
            )
            await state.set_state(RecordLessonStates.choosing_duration)
            await callback.message.edit_text(
                f"{_header(data)}Выберите длительность:",
                reply_markup=kb_duration(back_cb="lesson_back:kind"),
            )
        else:
            await state.update_data(selected_group_id=None, selected_ids=[])
            await _start_group_flow(
                callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, visibility,
                back_cb="lesson_back:duration",
                teacher_repo=teacher_repo, lesson_service=lesson_service,
            )

    elif target == "group_branch":
        # Возврат к выбору филиала из пикера группы.
        await state.update_data(selected_branch_id=None, selected_group_id=None)
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, visibility,
            back_cb="lesson_back:duration",
        )

    await callback.answer()

