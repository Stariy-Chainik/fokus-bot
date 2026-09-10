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
    StudentGroupRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_duration,
)
from bot.keyboards.calendar import kb_calendar

from ._base import router
from ._base import _header
from .flows import (
    _start_group_flow, _show_pair_list, _proceed_to_kind, _show_shared_group_picker,
    _show_rshare_branch_picker, _show_rshare_group_picker, _show_rshare_pool,
)
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("lesson_date:"), RecordLessonStates.choosing_date)
async def cb_lesson_date(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
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

    await _proceed_to_kind(callback, state, value, user, teacher_group_repo)
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
async def cb_rl_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    value = callback.data.split(":", 1)[1]
    if date.fromisoformat(value) > date.today():
        await callback.answer("Дата в будущем запрещена!", show_alert=True)
        return
    await _proceed_to_kind(callback, state, value, user, teacher_group_repo)
    await callback.answer()


# ─── Тип → длительность ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_kind:"), RecordLessonStates.choosing_kind)
async def cb_kind_any(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in ("group", "pair", "soloist", "shared", "rshare"):
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    if kind == "rshare":
        # Индивидуальные (revenue-share): длительность на суммы не влияет —
        # шаг пропускаем, пишем стандартные 60 мин, сразу выбор филиала.
        data = await state.get_data()
        gid = data.get("rshare_gid") or ""
        if not gid:
            await callback.answer("Группа не настроена", show_alert=True)
            return
        await state.update_data(
            kind="group", rshare_flow=True, duration_min=60,
            selected_group_id=gid, selected_ids=[],
        )
        await _show_rshare_branch_picker(
            callback, state, user, teacher_group_repo, group_repo, branch_repo,
            student_repo, student_group_repo,
        )
        await callback.answer()
        return
    await state.update_data(kind=kind, rshare_flow=False)
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
    branch_repo: BranchRepository, student_group_repo: StudentGroupRepository,
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



# ─── Индивидуальные (revenue-share): выбор филиала ──────────────────────────

@router.callback_query(F.data.startswith("rshb:"), RecordLessonStates.choosing_group_branch)
async def cb_rshare_branch(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    await _show_rshare_group_picker(
        callback, state, branch_id, user,
        teacher_group_repo, group_repo, student_repo, student_group_repo,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rshg:"), RecordLessonStates.choosing_group)
async def cb_rshare_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await _show_rshare_pool(
        callback, state, group_id, user,
        teacher_group_repo, group_repo, student_repo, student_group_repo,
    )
    await callback.answer()
