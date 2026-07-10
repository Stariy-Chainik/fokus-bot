from __future__ import annotations
import logging
from datetime import date

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
    kb_duration,
)
from bot.keyboards.calendar import kb_calendar

from ._base import router
from ._base import _header
from .flows import _start_group_flow, _show_pair_list, _proceed_to_kind, _show_shared_group_picker
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("lesson_date:"), RecordLessonStates.choosing_date)
async def cb_lesson_date(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    if value == "manual":
        today = date.today()
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=kb_calendar(
                today.year, today.month, prefix="rl",
                max_date=today, cancel_cb="teacher:cancel_lesson",
            ),
        )
        await callback.answer()
        return

    if date.fromisoformat(value) > date.today():
        await callback.answer("Дата в будущем запрещена!", show_alert=True)
        return

    await _proceed_to_kind(callback, state, value)
    await callback.answer()


@router.callback_query(F.data.startswith("rl_nav:"), RecordLessonStates.choosing_date)
async def cb_rl_nav(callback: CallbackQuery) -> None:
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(
            year, month, prefix="rl",
            max_date=date.today(), cancel_cb="teacher:cancel_lesson",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rl_pick:"), RecordLessonStates.choosing_date)
async def cb_rl_pick(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    if date.fromisoformat(value) > date.today():
        await callback.answer("Дата в будущем запрещена!", show_alert=True)
        return
    await _proceed_to_kind(callback, state, value)
    await callback.answer()


# ─── Тип → длительность ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_kind:"), RecordLessonStates.choosing_kind)
async def cb_kind_any(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in ("group", "pair", "soloist", "shared"):
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(kind=kind)
    await state.set_state(RecordLessonStates.choosing_duration)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Выберите длительность:",
        reply_markup=kb_duration(back_cb="lesson_back:kind"),
    )
    await callback.answer()


# ─── Длительность → ветка ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("duration:"), RecordLessonStates.choosing_duration)
async def cb_duration(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    duration = int(callback.data.split(":", 1)[1])
    await state.update_data(duration_min=duration)
    data = await state.get_data()
    kind = data.get("kind")

    if kind == "group":
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo,
            visibility, teacher_repo=teacher_repo, lesson_service=lesson_service,
        )

    elif kind == "pair":
        await _show_pair_list(callback, state, user, visibility)

    elif kind == "soloist":
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo,
            visibility, teacher_repo=teacher_repo, lesson_service=lesson_service,
        )

    elif kind == "shared":
        await state.update_data(
            selected_ids=[], selected_ids_by_group={},
            selected_group_id=None, group_auto=False,
        )
        await _show_shared_group_picker(callback, state, user, teacher_group_repo, group_repo)

    await callback.answer()

