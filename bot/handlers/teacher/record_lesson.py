from __future__ import annotations
"""
Педагог: FSM «Отметить занятие».
Порядок: Дата → Тип (группа/пара/соло) → Длительность → ветка → создание.
Группа: опциональная отметка присутствующих. Пара: выбор одной пары.
Соло: мульти-выбор учеников (включая тех, кто в паре — если пришли одни).
Защита от двойного нажатия — set _confirming_lesson_ids по tg_id.
"""
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import (
    TeacherRepository, StudentRepository, TeacherStudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
)
from bot.services import LessonService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_lesson_type_after_save, kb_duration, kb_teacher_menu,
    kb_attendance_yes_no, kb_pair_multi_select, kb_multi_select,
    kb_group_branch_picker, kb_group_picker,
)
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="teacher_record_lesson")

_confirming_lesson_ids: set[str] = set()

_KIND_LABEL = {"group": "Группа", "pair": "Пара", "soloist": "Соло"}


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _linked_students(teacher_id: str, ts_repo, student_repo):
    student_ids = await ts_repo.get_students_for_teacher(teacher_id)
    all_students = await student_repo.get_all()
    linked = [s for s in all_students if s.student_id in student_ids]
    return sorted(linked, key=lambda s: s.name)


async def _my_students_in_group(teacher_id: str, group_id: str, ts_repo, student_repo):
    """Ученики педагога, состоящие в указанной группе (строгое пересечение)."""
    mine_ids = set(await ts_repo.get_students_for_teacher(teacher_id))
    members = [
        s for s in await student_repo.get_all()
        if s.group_id == group_id and s.student_id in mine_ids
    ]
    members.sort(key=lambda s: s.name)
    return members


async def _all_students_in_group(group_id: str, student_repo):
    """Все ученики группы (без фильтра по педагогу) — для отметки присутствующих."""
    members = [s for s in await student_repo.get_all() if s.group_id == group_id]
    members.sort(key=lambda s: s.name)
    return members


def _date_picker_kb() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"lesson_date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"lesson_date:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="lesson_date:manual")],
        [InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson")],
    ])


def _header(data: dict) -> str:
    parts = []
    if data.get("lesson_date"):
        parts.append(f"Дата: {format_date_display(data['lesson_date'])}")
    if data.get("kind"):
        parts.append(f"Тип: {_KIND_LABEL.get(data['kind'], data['kind'])}")
    if data.get("duration_min"):
        parts.append(f"{data['duration_min']} мин")
    header = " | ".join(parts)
    return (f"<b>{header}</b>\n\n" if parts else "")


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
async def cb_cancel_lesson(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.", reply_markup=kb_teacher_menu())
    await callback.answer()


# ─── Назад на шаг ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_back:"))
async def cb_lesson_back(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
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
        await _show_pair_list(callback, state, user, ts_repo, student_repo)

    elif target == "group":
        # Возврат к пикеру группы (или филиала) из attendance yes/no.
        if data.get("group_auto"):
            # Авто-выбрана единственная группа → назад к длительности.
            await state.update_data(
                selected_group_id=None, selected_ids=[], group_auto=False,
            )
            await state.set_state(RecordLessonStates.choosing_duration)
            await callback.message.edit_text(
                f"{_header(data)}Выберите длительность:",
                reply_markup=kb_duration(back_cb="lesson_back:kind"),
            )
        else:
            await state.update_data(selected_group_id=None, selected_ids=[])
            await _start_group_flow(
                callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, ts_repo,
            )

    elif target == "group_branch":
        # Возврат к выбору филиала из пикера группы.
        await state.update_data(selected_branch_id=None, selected_group_id=None)
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, ts_repo,
        )

    await callback.answer()


async def _collect_pairs(teacher_id: str, ts_repo, student_repo):
    mine = await _linked_students(teacher_id, ts_repo, student_repo)
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
    ts_repo: TeacherStudentRepository,
) -> None:
    data = await state.get_data()
    group_ids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    all_groups = await group_repo.get_all()
    my_groups = [g for g in all_groups if g.group_id in group_ids]

    if not my_groups:
        await state.clear()
        await callback.message.edit_text(
            "У вас нет тренировочных групп. Обратитесь к администратору.",
            reply_markup=kb_teacher_menu(),
        )
        return

    if len(my_groups) == 1:
        # Единственная группа — авто-выбор, запоминаем флаг для корректного back.
        await state.update_data(group_auto=True)
        await _after_group_pick(
            callback, state, my_groups[0].group_id, user, ts_repo, student_repo, group_repo,
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
            reply_markup=kb_group_branch_picker(my_branches, back_cb="lesson_back:duration"),
        )
        return

    my_groups.sort(key=lambda g: g.name)
    await state.set_state(RecordLessonStates.choosing_group)
    await callback.message.edit_text(
        f"{_header(data)}Выберите группу:",
        reply_markup=kb_group_picker(my_groups, back_cb="lesson_back:duration"),
    )


async def _after_group_pick(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    user: User, ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    """Ветвление после выбора группы: соло → ростер учеников, группа → attendance."""
    data = await state.get_data()
    if data.get("kind") == "soloist":
        await _show_soloist_in_group(
            callback, state, group_id, user, ts_repo, student_repo, group_repo,
        )
    else:
        await _ask_group_attendance(callback, state, group_id, group_repo)


async def _ask_group_attendance(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    group_repo: GroupRepository,
) -> None:
    """После выбора группы — вопрос «Отметить присутствующих?»."""
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await state.update_data(selected_group_id=group_id, selected_ids=[])
    await state.set_state(RecordLessonStates.asking_attendance)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметить присутствующих?",
        reply_markup=kb_attendance_yes_no(),
    )


async def _show_soloist_in_group(
    callback: CallbackQuery, state: FSMContext, group_id: str,
    user: User, ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    """Для соло: мульти-выбор учеников педагога из выбранной группы."""
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    members = await _my_students_in_group(user.teacher_id, group_id, ts_repo, student_repo)
    if not members:
        await state.clear()
        await callback.message.edit_text(
            f"В группе «{group.name}» нет ваших учеников.\n"
            f"Добавьте их через «Мои ученики».",
            reply_markup=kb_teacher_menu(),
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
    user: User, ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    members = await _all_students_in_group(group_id, student_repo)
    if not members:
        await state.clear()
        await callback.message.edit_text(
            f"В группе «{group.name}» нет учеников. Обратитесь к администратору.",
            reply_markup=kb_teacher_menu(),
        )
        return
    await state.update_data(selected_group_id=group_id, selected_ids=[])
    await state.set_state(RecordLessonStates.selecting_attendees)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Группа: <b>{group.name}</b>\n"
        f"Отметьте присутствующих ({len(members)} в составе):",
        reply_markup=kb_multi_select(members, set(), back_cb="lesson_back:attendance", show_toggle_all=True),
    )


async def _show_pair_list(
    callback: CallbackQuery, state: FSMContext, user: User,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    data = await state.get_data()
    pairs = await _collect_pairs(user.teacher_id, ts_repo, student_repo)
    if not pairs:
        await callback.message.edit_text(
            "У вас нет пар среди ваших учеников.", reply_markup=kb_teacher_menu(),
        )
        await state.clear()
        return
    await state.update_data(selected_ids=[])
    await state.set_state(RecordLessonStates.choosing_pair)
    await callback.message.edit_text(
        f"{_header(data)}Отметьте пары ({len(pairs)} доступно), затем Подтвердить:",
        reply_markup=kb_pair_multi_select(pairs, set(), back_cb="lesson_back:duration"),
    )


# ─── Дата → тип ──────────────────────────────────────────────────────────────

async def _proceed_to_kind(callback: CallbackQuery, state: FSMContext, lesson_date: str) -> None:
    await state.update_data(lesson_date=lesson_date)
    await state.set_state(RecordLessonStates.choosing_kind)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
    )


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
    if kind not in ("group", "pair", "soloist"):
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
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
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
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, ts_repo,
        )

    elif kind == "pair":
        await _show_pair_list(callback, state, user, ts_repo, student_repo)

    elif kind == "soloist":
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, ts_repo,
        )

    await callback.answer()


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
    my_group_ids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
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
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await _after_group_pick(callback, state, group_id, user, ts_repo, student_repo, group_repo)
    await callback.answer()


# ─── Group: отметить присутствующих? ─────────────────────────────────────────

@router.callback_query(F.data == "attendance:no", RecordLessonStates.asking_attendance)
async def cb_attendance_no(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
) -> None:
    await state.update_data(selected_ids=[])
    await _finalize(callback, state, user, teacher_repo, None, lesson_service)


@router.callback_query(F.data == "attendance:yes", RecordLessonStates.asking_attendance)
async def cb_attendance_yes(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository, group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    gid = data.get("selected_group_id")
    if not gid:
        await callback.answer("Группа не выбрана", show_alert=True)
        return
    await _show_group_roster(callback, state, gid, user, ts_repo, student_repo, group_repo)
    await callback.answer()


# ─── Мульти-выбор: переключение и подтверждение ─────────────────────────────

async def _refresh_multi_select(
    callback: CallbackQuery, state: FSMContext, user: User,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    data = await state.get_data()
    selected = set(data.get("selected_ids", []))
    cur_state = await state.get_state()
    gid = data.get("selected_group_id")
    if cur_state == RecordLessonStates.selecting_soloists.state:
        if gid:
            mine = await _my_students_in_group(user.teacher_id, gid, ts_repo, student_repo)
            back_cb = "lesson_back:group"
        else:
            mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
            back_cb = "lesson_back:duration"
    elif cur_state == RecordLessonStates.selecting_attendees.state and gid:
        mine = await _all_students_in_group(gid, student_repo)
        back_cb = "lesson_back:attendance"
    else:
        mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
        back_cb = "lesson_back:attendance"
    show_toggle_all = bool(gid) and cur_state in (
        RecordLessonStates.selecting_attendees.state,
        RecordLessonStates.selecting_soloists.state,
    )
    await callback.message.edit_reply_markup(
        reply_markup=kb_multi_select(
            mine, selected, back_cb=back_cb, show_toggle_all=show_toggle_all,
        ),
    )


@router.callback_query(F.data.startswith("ms_toggle:"))
async def cb_ms_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
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
    await _refresh_multi_select(callback, state, user, ts_repo, student_repo)
    await callback.answer()


@router.callback_query(F.data == "ms_all")
async def cb_ms_all(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
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
        mine = await _all_students_in_group(gid, student_repo)
    elif gid and cur == RecordLessonStates.selecting_soloists.state:
        mine = await _my_students_in_group(user.teacher_id, gid, ts_repo, student_repo)
    else:
        mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
    all_ids = [s.student_id for s in mine]
    selected = list(data.get("selected_ids", []))
    new_selected = [] if len(selected) == len(all_ids) else all_ids
    await state.update_data(selected_ids=new_selected)
    await _refresh_multi_select(callback, state, user, ts_repo, student_repo)
    await callback.answer()


@router.callback_query(F.data == "ms_confirm")
async def cb_ms_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository,
    lesson_service: LessonService,
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
    await _finalize(callback, state, user, teacher_repo, student_repo, lesson_service)


# ─── Pair: мульти-выбор пар ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pair_toggle:"), RecordLessonStates.choosing_pair)
async def cb_pair_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
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

    pairs = await _collect_pairs(user.teacher_id, ts_repo, student_repo)
    await callback.message.edit_reply_markup(
        reply_markup=kb_pair_multi_select(pairs, set(selected), back_cb="lesson_back:duration"),
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


# ─── Создание занятий ────────────────────────────────────────────────────────

async def _finalize(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository | None,
    lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    lock_key = str(callback.from_user.id)
    if lock_key in _confirming_lesson_ids:
        logger.warning("Двойное подтверждение занятия tg_id=%s", callback.from_user.id)
        await callback.answer("Занятие уже сохраняется, подождите.", show_alert=True)
        return
    _confirming_lesson_ids.add(lock_key)

    try:
        data = await state.get_data()
        teacher = await teacher_repo.get_by_id(user.teacher_id)
        if not teacher:
            await state.clear()
            await callback.message.edit_text("Педагог не найден. Обратитесь к администратору.")
            return

        kind = data.get("kind")
        lesson_date = data["lesson_date"]
        duration = int(data["duration_min"])

        if kind == "group":
            attendee_ids = list(data.get("selected_ids", []))
            attendees_csv = ",".join(attendee_ids) if attendee_ids else None
            group_id = data.get("selected_group_id") or ""
            lesson = await lesson_service.create(
                teacher=teacher,
                lesson_type=LessonType.GROUP,
                lesson_date=lesson_date,
                duration_min=duration,
                attendees=attendees_csv,
                group_id=group_id,
            )
            extra = f"\nОтмечено: {len(attendee_ids)}" if attendee_ids else ""
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"<b>✅ Групповое занятие записано</b>\nID: {lesson.lesson_id}\n"
                f"Дата: {format_date_display(lesson.date)}{extra}\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type_after_save(),
            )

        elif kind == "pair":
            keys = list(data.get("selected_ids", []))
            pairs_data: list[tuple[str, str, str, str]] = []
            pair_labels: list[str] = []
            for a_id in keys:
                a = await student_repo.get_by_id(a_id)
                if not a or not a.partner_id:
                    continue
                b = await student_repo.get_by_id(a.partner_id)
                if not b:
                    continue
                pairs_data.append((a.student_id, a.name, b.student_id, b.name))
                pair_labels.append(f"{a.name} ↔ {b.name}")
            lessons = await lesson_service.create_pair_batch(
                teacher=teacher,
                lesson_date=lesson_date,
                duration_min=duration,
                pairs=pairs_data,
            )
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"<b>✅ Создано парных занятий: {len(lessons)}</b>\n"
                f"Дата: {format_date_display(lesson_date)}\n"
                f"Пары: {'; '.join(pair_labels)}\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type_after_save(),
            )

        elif kind == "soloist":
            ids = list(data.get("selected_ids", []))
            students = []
            for sid in ids:
                s = await student_repo.get_by_id(sid)
                if s:
                    students.append((s.student_id, s.name))
            lessons = await lesson_service.create_soloist_batch(
                teacher=teacher,
                lesson_date=lesson_date,
                duration_min=duration,
                students=students,
            )
            names = ", ".join(n for _, n in students)
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"<b>✅ Создано соло-занятий: {len(lessons)}</b>\n"
                f"Дата: {format_date_display(lesson_date)}\n"
                f"Ученики: {names}\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type_after_save(),
            )
        else:
            await state.clear()
            await callback.message.edit_text(
                "Неизвестный тип занятия.", reply_markup=kb_teacher_menu(),
            )
    except PermissionError as exc:
        await state.clear()
        await callback.message.edit_text(
            f"🔒 {exc}\nОбратитесь к администратору.", reply_markup=kb_teacher_menu(),
        )
    except ValueError as exc:
        await callback.message.edit_text(f"Ошибка: {exc}", reply_markup=kb_teacher_menu())
    except Exception as exc:
        logger.error("Ошибка записи занятия: %s", exc)
        await callback.message.edit_text(
            "Ошибка при сохранении занятия. Попробуйте позже.", reply_markup=kb_teacher_menu(),
        )
    finally:
        _confirming_lesson_ids.discard(lock_key)

    await callback.answer()
