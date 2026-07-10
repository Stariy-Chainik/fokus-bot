from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models import User
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, TeacherGroupRepository, StudentGroupRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_group_picker,
)

from ._base import router
from ._base import _tid, _header
from .flows import _after_group_pick, _show_group_roster
from .finalize import _finalize
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


# ─── Group: выбор филиала/группы ────────────────────────────────────────────

@router.callback_query(F.data.startswith("group_branch:"), RecordLessonStates.choosing_group_branch)
async def cb_group_branch(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    my_group_ids = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
    groups = sorted(
        [g for g in await group_repo.get_all()
         if g.group_id in my_group_ids and g.branch_id == branch_id],
        key=lambda g: g.name,
    )
    await state.update_data(selected_branch_id=branch_id)
    await state.set_state(RecordLessonStates.choosing_group)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Выберите группу:",
        reply_markup=kb_group_picker(groups, back_cb="lesson_back:group_branch"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group_pick:"), RecordLessonStates.choosing_group)
async def cb_group_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await _after_group_pick(
        callback, state, group_id, user, visibility, student_repo, group_repo,
        teacher_repo, lesson_service,
    )
    await callback.answer()


# ─── Group: отметить присутствующих? ─────────────────────────────────────────

@router.callback_query(F.data == "attendance:no", RecordLessonStates.asking_attendance)
async def cb_attendance_no(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
    group_repo: GroupRepository,
) -> None:
    await state.update_data(selected_ids=[])
    await _finalize(callback, state, user, teacher_repo, None, lesson_service, group_repo)


@router.callback_query(F.data == "attendance:yes", RecordLessonStates.asking_attendance)
async def cb_attendance_yes(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    gid = data.get("selected_group_id")
    if not gid:
        await callback.answer("Группа не выбрана", show_alert=True)
        return
    await _show_group_roster(callback, state, gid, user, student_repo, group_repo, student_group_repo, teacher_group_repo)
    await callback.answer()


