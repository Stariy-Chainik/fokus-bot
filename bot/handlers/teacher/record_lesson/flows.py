from __future__ import annotations
import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models import User, GroupBillingMode
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_attendance_yes_no, kb_pair_multi_select, kb_multi_select,
    kb_group_roster_per_visit, kb_group_branch_picker, kb_group_picker,
    kb_shared_group_picker,
)

from ._base import _tid, _menu_kb, _all_students_in_group, _header
from .finalize import _finalize

logger = logging.getLogger(__name__)


async def _collect_pairs(teacher_id: str, visibility: TeacherVisibilityService):
    mine = await visibility.students_for_teacher(teacher_id)
    mine_ids = {s.student_id for s in mine}
    by_id = {s.student_id: s for s in mine}
    seen: set[tuple[str, str]] = set()
    pairs = []
    for s in mine:
        if not s.partner_id or s.partner_id not in mine_ids:
            continue
        partner = by_id[s.partner_id]
        key = tuple(sorted([s.student_id, partner.student_id]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((s, partner))
    return pairs


async def _start_group_flow(
    callback: CallbackQuery, state: FSMContext, user: User,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository, student_repo: StudentRepository,
    visibility: TeacherVisibilityService,
    back_cb: str = "lesson_back:duration",
    teacher_repo: TeacherRepository | None = None,
    lesson_service: LessonService | None = None,
) -> None:
    data = await state.get_data()
    group_ids = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
    all_groups = await group_repo.get_all()
    my_groups = [g for g in all_groups if g.group_id in group_ids]

    if not my_groups:
        await state.clear()
        await callback.message.edit_text(
            "У вас нет тренировочных групп. Обратитесь к администратору.",
            reply_markup=_menu_kb(user, data),
        )
        return

    if len(my_groups) == 1:
        # Единственная группа — авто-выбор, запоминаем флаг для корректного back.
        await state.update_data(group_auto=True)
        await _after_group_pick(
            callback, state, my_groups[0].group_id, user, visibility, student_repo, group_repo,
            teacher_repo, lesson_service,
        )
        return

    await state.update_data(group_auto=False)
    branch_ids = {g.branch_id for g in my_groups}
    if len(branch_ids) > 1:
        all_branches = await branch_repo.get_all()
        my_branches = sorted(
            [b for b in all_branches if b.branch_id in branch_ids], key=lambda b: b.name,
        )
        await state.set_state(RecordLessonStates.choosing_group_branch)
        await callback.message.edit_text(
            f"{_header(data)}Выберите филиал:",
            reply_markup=kb_group_branch_picker(my_branches, back_cb=back_cb),
        )
        return

    my_groups.sort(key=lambda g: g.name)
    await state.set_state(RecordLessonStates.choosing_group)
    await callback.message.edit_text(
        f"{_header(data)}Выберите группу:",
        reply_markup=kb_group_picker(my_groups, back_cb=back_cb),
    )


async def _after_group_pick(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    user: User, visibility: TeacherVisibilityService,
    student_repo: StudentRepository, group_repo: GroupRepository,
    teacher_repo: TeacherRepository | None = None,
    lesson_service: LessonService | None = None,
) -> None:
    """Ветвление после выбора группы:
    соло → ростер учеников; группа → вопрос attendance (или автосохранение)."""
    data = await state.get_data()
    if data.get("kind") == "soloist":
        await _show_soloist_in_group(
            callback, state, group_id, user, visibility, group_repo,
        )
    else:
        await _ask_group_attendance(
            callback, state, group_id, group_repo, user, teacher_repo, lesson_service,
        )


async def _ask_group_attendance(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    group_repo: GroupRepository,
    user: User | None = None,
    teacher_repo: TeacherRepository | None = None,
    lesson_service: LessonService | None = None,
) -> None:
    """После выбора группы.
    PER_VISIT: вопрос «Отметить присутствующих?».
    NONE: автосохранение без лишнего клика.
    """
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await state.update_data(selected_group_id=group_id, selected_ids=[])
    await state.set_state(RecordLessonStates.asking_attendance)

    if group.billing_mode != GroupBillingMode.PER_VISIT and teacher_repo and lesson_service:
        await _finalize(callback, state, user, teacher_repo, None, lesson_service, group_repo)
        return

    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметить присутствующих?",
        reply_markup=kb_attendance_yes_no(),
    )


async def _show_soloist_in_group(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    user: User, visibility: TeacherVisibilityService,
    group_repo: GroupRepository,
) -> None:
    """Для соло: мульти-выбор учеников педагога из выбранной группы."""
    data = await state.get_data()
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    members = await visibility.students_in_group_for_teacher(_tid(user, data), group_id)
    if not members:
        await state.clear()
        await callback.message.edit_text(
            f"В группе «{group.name}» нет учеников.",
            reply_markup=_menu_kb(user, data),
        )
        return
    await state.update_data(selected_group_id=group_id, selected_ids=[])
    await state.set_state(RecordLessonStates.selecting_soloists)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметьте учеников для соло ({len(members)} в группе), затем Подтвердить.\n"
        f"💡 Можно отметить и ученика из пары — если он пришёл один.",
        reply_markup=kb_multi_select(members, set(), back_cb="lesson_back:group"),
    )


async def _show_group_roster(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    user: User,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository | None = None,
) -> None:
    proxy_data = await state.get_data()
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    members = await _all_students_in_group(group_id, student_repo, student_group_repo)
    if not members:
        await state.clear()
        await callback.message.edit_text(
            f"В группе «{group.name}» нет учеников. Обратитесь к администратору.",
            reply_markup=_menu_kb(user, proxy_data),
        )
        return
    state_update: dict = {
        "selected_group_id": group_id, "selected_ids": [], "extra_student_ids": [],
        "has_other_groups": False,
    }
    if group.billing_mode == GroupBillingMode.PER_VISIT:
        tiers: dict = {s.student_id: s.group_tier.value for s in members}
        # Узнаём есть ли у педагога другие группы (для кнопки «Добавить из других групп»)
        if teacher_group_repo is not None:
            teacher_id = _tid(user, proxy_data)
            all_teacher_gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
            state_update["has_other_groups"] = bool(all_teacher_gids - {group_id})
        state_update["per_visit_tiers"] = tiers
    else:
        state_update["per_visit_tiers"] = {}
    await state.update_data(**state_update)
    await state.set_state(RecordLessonStates.selecting_attendees)
    data = await state.get_data()

    if group.billing_mode == GroupBillingMode.PER_VISIT:
        text = (
            f"{_header(data)}Группа: <b>{group.name}</b>\n"
            f"Отметьте присутствующих ({len(members)} в составе).\n"
            f"<i>Кнопка справа — текущий тариф (↕ нажмите чтобы сменить).</i>"
        )
        kb = kb_group_roster_per_visit(
            members, set(), data["per_visit_tiers"],
            group.price_short, group.duration_short,
            group.price_full, group.duration_full,
            back_cb="lesson_back:attendance",
            extra_students=None,
            show_add_other=state_update["has_other_groups"],
        )
    else:
        text = (
            f"{_header(data)}Группа: <b>{group.name}</b>\n"
            f"Отметьте присутствующих ({len(members)} в составе):"
        )
        kb = kb_multi_select(members, set(), back_cb="lesson_back:attendance", show_toggle_all=True)
    await callback.message.edit_text(text, reply_markup=kb)


async def _collect_soloists(teacher_id: str, visibility: TeacherVisibilityService):
    mine = await visibility.students_for_teacher(teacher_id)
    return sorted(
        [s for s in mine if not s.partner_id],
        key=lambda s: s.name,
    )


async def _show_pair_list(
    callback: CallbackQuery, state: FSMContext, user: User,
    visibility: TeacherVisibilityService,
) -> None:
    data = await state.get_data()
    pairs = await _collect_pairs(_tid(user, data), visibility)
    if not pairs:
        await callback.message.edit_text(
            "У вас нет сформированных пар.",
            reply_markup=_menu_kb(user, data),
        )
        await state.clear()
        return
    await state.update_data(selected_ids=[])
    await state.set_state(RecordLessonStates.choosing_pair)
    text = f"{_header(data)}Отметьте пары ({len(pairs)} доступно), затем Подтвердить."
    await callback.message.edit_text(
        text,
        reply_markup=kb_pair_multi_select(
            pairs, set(),
            back_cb="lesson_back:duration",
        ),
    )



async def _proceed_to_kind(callback: CallbackQuery, state: FSMContext, lesson_date: str) -> None:
    await state.update_data(lesson_date=lesson_date)
    await state.set_state(RecordLessonStates.choosing_kind)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
    )


async def _refresh_multi_select(
    callback: CallbackQuery, state: FSMContext, user: User,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    group_repo: GroupRepository | None = None,
    student_group_repo: StudentGroupRepository | None = None,
) -> None:
    data = await state.get_data()
    selected = set(data.get("selected_ids", []))
    cur_state = await state.get_state()
    gid = data.get("selected_group_id")
    if cur_state == RecordLessonStates.selecting_soloists.state:
        if gid:
            mine = await visibility.students_in_group_for_teacher(_tid(user, data), gid)
            back_cb = "lesson_back:group"
        else:
            mine = await visibility.students_for_teacher(_tid(user, data))
            back_cb = "lesson_back:duration"
    elif cur_state == RecordLessonStates.selecting_attendees.state and gid:
        assert student_group_repo is not None, "student_group_repo required for attendance roster"
        mine = await _all_students_in_group(gid, student_repo, student_group_repo)
        back_cb = "lesson_back:attendance"
    else:
        mine = await visibility.students_for_teacher(_tid(user, data))
        back_cb = "lesson_back:attendance"

    per_visit_tiers = data.get("per_visit_tiers") or {}
    group = None
    if per_visit_tiers and gid and group_repo is not None:
        group = await group_repo.get_by_id(gid)

    if group is not None and group.billing_mode == GroupBillingMode.PER_VISIT:
        extra_student_ids = set(data.get("extra_student_ids") or [])
        if extra_student_ids and student_group_repo is not None:
            all_students = await student_repo.get_all()
            by_id = {s.student_id: s for s in all_students}
            extra_students = sorted(
                [by_id[sid] for sid in extra_student_ids if sid in by_id],
                key=lambda s: s.name,
            )
        else:
            extra_students = []
        kb = kb_group_roster_per_visit(
            mine, selected, per_visit_tiers,
            group.price_short, group.duration_short,
            group.price_full, group.duration_full,
            back_cb=back_cb,
            extra_students=extra_students or None,
            show_add_other=bool(data.get("has_other_groups")),
        )
    else:
        show_toggle_all = bool(gid) and cur_state in (
            RecordLessonStates.selecting_attendees.state,
            RecordLessonStates.selecting_soloists.state,
        )
        kb = kb_multi_select(
            mine, selected, back_cb=back_cb, show_toggle_all=show_toggle_all,
        )
    await callback.message.edit_reply_markup(reply_markup=kb)



async def _show_shared_group_picker(
    callback: CallbackQuery, state: FSMContext, user: User,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    data = await state.get_data()
    group_ids = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
    all_groups = sorted(
        [g for g in await group_repo.get_all() if g.group_id in group_ids],
        key=lambda g: g.name,
    )
    if not all_groups:
        await state.clear()
        await callback.message.edit_text(
            "У вас нет групп. Обратитесь к администратору.",
            reply_markup=_menu_kb(user, data),
        )
        return
    by_group: dict = data.get("selected_ids_by_group") or {}
    counts = {gid: len(ids) for gid, ids in by_group.items()}
    total = sum(counts.values())
    await state.set_state(RecordLessonStates.choosing_shared_group)
    await callback.message.edit_text(
        f"{_header(data)}Выберите группу чтобы добавить учеников:",
        reply_markup=kb_shared_group_picker(all_groups, counts, total),
    )


