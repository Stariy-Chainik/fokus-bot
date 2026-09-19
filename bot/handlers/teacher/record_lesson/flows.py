from __future__ import annotations
import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models import User, GroupBillingMode
from bot.utils.groups import hide_service_groups
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
    kb_shared_group_picker, kb_rshare_branch_picker, kb_rshare_group_picker,
)

from ._base import _tid, _menu_kb, _all_students_in_group, _header
from .finalize import _finalize

logger = logging.getLogger(__name__)


async def _collect_pairs(teacher_id: str, visibility: TeacherVisibilityService):
    mine = await visibility.students_for_teacher(teacher_id)
    mine_ids = {s.student_id for s in mine}
    by_id = {s.student_id: s for s in mine}
    seen: set[tuple[str, ...]] = set()
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
    group_ids.discard(data.get("rshare_gid") or "")  # инд. Яковлевой — не «Групповое»
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
    back_cb: str = "lesson_back:attendance",
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
            f"<i>Кнопка справа — тариф на это занятие (↕ сменить: полный → "
            f"{'короткий → ' if group.price_short > 0 else ''}🆓 пробное).</i>"
        )
        kb = kb_group_roster_per_visit(
            members, set(), data["per_visit_tiers"],
            group.price_short, group.duration_short,
            group.price_full, group.duration_full,
            back_cb=back_cb,
            extra_students=None,
            show_add_other=state_update["has_other_groups"],
        )
    else:
        text = (
            f"{_header(data)}Группа: <b>{group.name}</b>\n"
            f"Отметьте присутствующих ({len(members)} в составе):"
        )
        kb = kb_multi_select(members, set(), back_cb=back_cb, show_toggle_all=True)
    await callback.message.edit_text(text, reply_markup=kb)


async def _rshare_branches(
    data: dict, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> list:
    """Филиалы, где у педагога есть группы (кроме технической revenue-share)."""
    gids = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
    gids.discard(data.get("rshare_gid") or "")
    groups_by_id = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
    branch_ids = set()
    for gid in gids:
        g = groups_by_id.get(gid)
        if g:
            branch_ids.add(g.branch_id)
    return sorted(
        [b for b in await branch_repo.get_all() if b.branch_id in branch_ids],
        key=lambda b: b.name,
    )


async def _show_rshare_branch_picker(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    data = await state.get_data()
    branches = await _rshare_branches(data, user, teacher_group_repo, group_repo, branch_repo)
    if not branches:
        await state.clear()
        await callback.message.edit_text(
            "У вас нет групп с ученицами. Обратитесь к администратору.",
            reply_markup=_menu_kb(user, data),
        )
        return
    if len(branches) == 1:
        await _show_rshare_group_picker(
            callback, state, branches[0].branch_id, user,
            teacher_group_repo, group_repo, student_repo, student_group_repo,
        )
        return
    total = len(data.get("selected_ids") or [])
    await state.set_state(RecordLessonStates.choosing_group_branch)
    await callback.message.edit_text(
        f"{_header(data)}Из какого филиала участницы?"
        + (f"\nУже отмечено: {total}" if total else ""),
        reply_markup=kb_rshare_branch_picker(branches, total_selected=total),
    )


async def _rshare_groups_in_branch(
    data: dict, user: User | None, branch_id: str,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> list:
    gids = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
    gids.discard(data.get("rshare_gid") or "")
    groups_by_id = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
    groups = []
    for gid in gids:
        g = groups_by_id.get(gid)
        if g and g.branch_id == branch_id:
            groups.append(g)
    return sorted(groups, key=lambda g: g.name)


async def _show_rshare_group_picker(
    callback: CallbackQuery, state: FSMContext, branch_id: str, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    """Фильтр по группам внутри филиала. Одна группа — сразу список учениц."""
    data = await state.get_data()
    groups = await _rshare_groups_in_branch(data, user, branch_id, teacher_group_repo, group_repo)
    if not groups:
        await callback.answer("В этом филиале нет ваших групп", show_alert=True)
        return
    await state.update_data(rshare_branch_id=branch_id)
    if len(groups) == 1:
        await _show_rshare_pool(
            callback, state, groups[0].group_id, user,
            teacher_group_repo, group_repo, student_repo, student_group_repo,
        )
        return
    selected = set(data.get("selected_ids") or [])
    counts = {}
    for g in groups:
        member_ids = set(await student_group_repo.get_students_for_group(g.group_id))
        counts[g.group_id] = len(selected & member_ids)
    await state.set_state(RecordLessonStates.choosing_group)
    await callback.message.edit_text(
        f"{_header(data)}Выберите группу:"
        + (f"\nОтмечено всего: {len(selected)}" if selected else ""),
        reply_markup=kb_rshare_group_picker(groups, counts, total_selected=len(selected)),
    )


async def _show_rshare_pool(
    callback: CallbackQuery, state: FSMContext, group_id: str, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    """Ученицы одной группы для индивидуального занятия."""
    data = await state.get_data()
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    students = sorted(
        await _all_students_in_group(group_id, student_repo, student_group_repo),
        key=lambda s: s.name,
    )
    if not students:
        await callback.answer("В группе нет учениц", show_alert=True)
        return
    branch_id = data.get("rshare_branch_id") or group.branch_id
    many_groups = len(await _rshare_groups_in_branch(
        data, user, branch_id, teacher_group_repo, group_repo,
    )) > 1
    back_cb = "lesson_back:rshare_group" if many_groups else "lesson_back:rshare_branch"
    selected = set(data.get("selected_ids") or [])
    await state.update_data(
        rshare_pool_ids=[s.student_id for s in students], rshare_pool_back=back_cb,
        per_visit_tiers={}, extra_student_ids=[], has_other_groups=False,
    )
    await state.set_state(RecordLessonStates.selecting_attendees)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметьте участниц (1–3). Отмечено всего: {len(selected)}.\n"
        f"«Назад» — другая группа/филиал, отметки сохранятся.",
        reply_markup=kb_multi_select(students, selected, back_cb=back_cb),
    )


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



async def _proceed_to_kind(
    callback: CallbackQuery, state: FSMContext, lesson_date: str,
    user: User | None = None,
    teacher_group_repo: TeacherGroupRepository | None = None,
) -> None:
    data = await state.get_data()
    # Педагог с revenue-share группой (Яковлева): упрощённое меню
    # «Групповое / Индивидуальное»; rshare_gid запоминаем на весь флоу.
    rshare_gid = ""
    if user is not None and teacher_group_repo is not None:
        from config.settings import settings
        share_gids = set(settings.revenue_share_group_map)
        if share_gids:
            own = set(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
            rshare_gid = next(iter(own & share_gids), "")
    await state.update_data(lesson_date=lesson_date, rshare_gid=rshare_gid)
    await state.set_state(RecordLessonStates.choosing_kind)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(simple=bool(rshare_gid)),
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
    elif cur_state == RecordLessonStates.selecting_attendees.state and data.get("rshare_pool_ids"):
        pool = set(data["rshare_pool_ids"])
        mine = sorted(
            [s for s in await student_repo.get_all() if s.student_id in pool],
            key=lambda s: s.name,
        )
        back_cb = data.get("rshare_pool_back") or "lesson_back:rshare_branch"
    elif cur_state == RecordLessonStates.selecting_attendees.state and gid:
        assert student_group_repo is not None, "student_group_repo required for attendance roster"
        mine = await _all_students_in_group(gid, student_repo, student_group_repo)
        back_cb = "lesson_back:duration" if data.get("rshare_flow") else "lesson_back:attendance"
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
    group_ids = hide_service_groups(await teacher_group_repo.get_groups_for_teacher(_tid(user, data)))
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


