from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models import User, StudentGroupTier
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, TeacherGroupRepository, StudentGroupRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_group_roster_per_visit, kb_other_groups_picker, kb_other_group_students,
)

from ._base import router
from ._base import _tid, _all_students_in_group, _header
from .flows import _refresh_multi_select
from .finalize import _finalize
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("ms_toggle:"))
async def cb_ms_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    cur = await state.get_state()
    if cur not in (RecordLessonStates.selecting_attendees.state, RecordLessonStates.selecting_soloists.state):
        await callback.answer()
        return

    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if student_id in selected:
        selected.remove(student_id)
    else:
        selected.append(student_id)
    await state.update_data(selected_ids=selected)
    await _refresh_multi_select(
        callback, state, user, visibility, student_repo, group_repo, student_group_repo,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ms_tier:"))
async def cb_ms_tier(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    cur = await state.get_state()
    if cur != RecordLessonStates.selecting_attendees.state:
        await callback.answer()
        return
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    tiers = dict(data.get("per_visit_tiers") or {})
    cur_tier = tiers.get(student_id, StudentGroupTier.FULL.value)
    new_tier = (
        StudentGroupTier.FULL.value if cur_tier == StudentGroupTier.SHORT.value
        else StudentGroupTier.SHORT.value
    )
    tiers[student_id] = new_tier
    await state.update_data(per_visit_tiers=tiers)
    await _refresh_multi_select(
        callback, state, user, visibility, student_repo, group_repo, student_group_repo,
    )
    await callback.answer(f"Тариф на это занятие: {'короткий' if new_tier == 'short' else 'полный'}")


@router.callback_query(F.data == "ms_all")
async def cb_ms_all(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    cur = await state.get_state()
    if cur not in (RecordLessonStates.selecting_attendees.state, RecordLessonStates.selecting_soloists.state):
        await callback.answer()
        return
    data = await state.get_data()
    gid = data.get("selected_group_id")
    if gid and cur == RecordLessonStates.selecting_attendees.state:
        mine = await _all_students_in_group(gid, student_repo, student_group_repo)
    elif gid and cur == RecordLessonStates.selecting_soloists.state:
        mine = await visibility.students_in_group_for_teacher(_tid(user, data), gid)
    else:
        mine = await visibility.students_for_teacher(_tid(user, data))
    all_ids = [s.student_id for s in mine]
    selected = list(data.get("selected_ids", []))
    extra_ids = set(data.get("extra_student_ids") or [])
    selected_main = [sid for sid in selected if sid not in extra_ids]
    selected_extra = [sid for sid in selected if sid in extra_ids]
    if len(selected_main) == len(all_ids):
        new_selected = selected_extra  # снять основной состав, оставить extra
    else:
        new_selected = all_ids + selected_extra  # добавить основной состав, оставить extra
    await state.update_data(selected_ids=new_selected)
    await _refresh_multi_select(
        callback, state, user, visibility, student_repo, group_repo, student_group_repo,
    )
    await callback.answer()


@router.callback_query(F.data == "ms_confirm")
async def cb_ms_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService, group_repo: GroupRepository,
) -> None:
    cur = await state.get_state()
    if cur not in (RecordLessonStates.selecting_attendees.state, RecordLessonStates.selecting_soloists.state):
        await callback.answer()
        return
    data = await state.get_data()
    selected = list(data.get("selected_ids", []))
    if not selected:
        await callback.answer("Никто не отмечен", show_alert=True)
        return
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service, group_repo)


# ─── Добавление учеников из других групп педагога ───────────────────────────

@router.callback_query(F.data == "ms_add_other", RecordLessonStates.selecting_attendees)
async def cb_ms_add_other(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    main_gid = data.get("selected_group_id")
    teacher_id = _tid(user, data)
    all_gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    other_gids = all_gids - {main_gid}
    groups = []
    for gid in sorted(other_gids):
        g = await group_repo.get_by_id(gid)
        if g:
            groups.append(g)
    groups.sort(key=lambda g: g.name)
    if not groups:
        await callback.answer("У вас нет других групп.", show_alert=True)
        return
    await callback.message.edit_text(
        "<b>Из какой группы добавить учеников?</b>",
        reply_markup=kb_other_groups_picker(groups),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ms_other_group:"), RecordLessonStates.selecting_attendees)
async def cb_ms_other_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    other_gid = callback.data.split(":", 1)[1]
    data = await state.get_data()
    main_gid = data.get("selected_group_id")
    main_member_ids = set(await student_group_repo.get_students_for_group(main_gid)) if main_gid else set()
    students = [
        s for s in await _all_students_in_group(other_gid, student_repo, student_group_repo)
        if s.student_id not in main_member_ids
    ]
    if not students:
        await callback.answer("В этой группе нет учеников вне основного состава.", show_alert=True)
        return
    # Инициализируем временные выборки на основе уже добавленных extras
    current_extras = set(data.get("extra_student_ids") or [])
    picked = [s.student_id for s in students if s.student_id in current_extras]
    await state.update_data(extra_pick_group_id=other_gid, extra_pick_ids=picked)
    group = await group_repo.get_by_id(other_gid)
    await callback.message.edit_text(
        f"<b>Из группы «{group.name if group else other_gid}»</b>\n"
        f"Отметьте учеников для добавления в ростер:",
        reply_markup=kb_other_group_students(students, set(picked)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ms_other_pick:"), RecordLessonStates.selecting_attendees)
async def cb_ms_other_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    other_gid = data.get("extra_pick_group_id")
    if not other_gid:
        await callback.answer()
        return
    picked = list(data.get("extra_pick_ids") or [])
    if student_id in picked:
        picked.remove(student_id)
    else:
        picked.append(student_id)
    await state.update_data(extra_pick_ids=picked)

    main_gid = data.get("selected_group_id")
    main_member_ids = set(await student_group_repo.get_students_for_group(main_gid)) if main_gid else set()
    students = [
        s for s in await _all_students_in_group(other_gid, student_repo, student_group_repo)
        if s.student_id not in main_member_ids
    ]
    await callback.message.edit_reply_markup(
        reply_markup=kb_other_group_students(students, set(picked)),
    )
    await callback.answer()


@router.callback_query(F.data == "ms_other_confirm", RecordLessonStates.selecting_attendees)
async def cb_ms_other_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    other_gid = data.get("extra_pick_group_id")
    picked = set(data.get("extra_pick_ids") or [])

    # Учеников из этой группы убираем из extras, потом докидываем новых picked
    current_extras = list(data.get("extra_student_ids") or [])
    if other_gid:
        from_this_group = set(await student_group_repo.get_students_for_group(other_gid))
        current_extras = [sid for sid in current_extras if sid not in from_this_group]
    # Добавляем выбранных (без дублей)
    for sid in picked:
        if sid not in current_extras:
            current_extras.append(sid)

    # Тарифы для новых extras — full по умолчанию
    tiers = dict(data.get("per_visit_tiers") or {})
    for sid in current_extras:
        tiers.setdefault(sid, StudentGroupTier.FULL.value)

    await state.update_data(
        extra_student_ids=current_extras,
        per_visit_tiers=tiers,
        extra_pick_group_id=None,
        extra_pick_ids=[],
    )

    # Перерисуем основной ростер
    main_gid = data.get("selected_group_id")
    group = await group_repo.get_by_id(main_gid) if main_gid else None
    members = await _all_students_in_group(main_gid, student_repo, student_group_repo) if main_gid else []
    extras_objs = []
    if current_extras:
        by_id = {s.student_id: s for s in await student_repo.get_all()}
        extras_objs = sorted(
            [by_id[sid] for sid in current_extras if sid in by_id], key=lambda s: s.name,
        )
    selected = set(data.get("selected_ids", []))
    text = (
        f"{_header(data)}Группа: <b>{group.name if group else main_gid}</b>\n"
        f"Отметьте присутствующих ({len(members)} в составе"
        f"{f' + {len(extras_objs)} из других групп' if extras_objs else ''}).\n"
        f"<i>Кнопка справа — текущий тариф (↕ нажмите чтобы сменить).</i>"
    )
    kb = kb_group_roster_per_visit(
        members, selected, tiers,
        group.price_short if group else 0, group.duration_short if group else 0,
        group.price_full if group else 0, group.duration_full if group else 0,
        back_cb="lesson_back:attendance",
        extra_students=extras_objs or None,
        show_add_other=bool(data.get("has_other_groups")),
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Добавлено" if picked else "Без изменений")


@router.callback_query(F.data == "ms_other_cancel", RecordLessonStates.selecting_attendees)
async def cb_ms_other_cancel(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(extra_pick_group_id=None, extra_pick_ids=[])
    # Возвращаемся в основной ростер
    data = await state.get_data()
    main_gid = data.get("selected_group_id")
    group = await group_repo.get_by_id(main_gid) if main_gid else None
    members = await _all_students_in_group(main_gid, student_repo, student_group_repo) if main_gid else []
    current_extras = list(data.get("extra_student_ids") or [])
    extras_objs = []
    if current_extras:
        by_id = {s.student_id: s for s in await student_repo.get_all()}
        extras_objs = sorted(
            [by_id[sid] for sid in current_extras if sid in by_id], key=lambda s: s.name,
        )
    selected = set(data.get("selected_ids", []))
    tiers = dict(data.get("per_visit_tiers") or {})
    text = (
        f"{_header(data)}Группа: <b>{group.name if group else main_gid}</b>\n"
        f"Отметьте присутствующих ({len(members)} в составе"
        f"{f' + {len(extras_objs)} из других групп' if extras_objs else ''}).\n"
        f"<i>Кнопка справа — текущий тариф (↕ нажмите чтобы сменить).</i>"
    )
    kb = kb_group_roster_per_visit(
        members, selected, tiers,
        group.price_short if group else 0, group.duration_short if group else 0,
        group.price_full if group else 0, group.duration_full if group else 0,
        back_cb="lesson_back:attendance",
        extra_students=extras_objs or None,
        show_add_other=bool(data.get("has_other_groups")),
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

