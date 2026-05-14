from __future__ import annotations
"""Педагог: «Группы» — просмотр своих групп, управление составом."""
import logging
import uuid
from collections import defaultdict
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from dateutil.relativedelta import relativedelta  # type: ignore

from bot.models import User
from bot.models.enums import GroupBillingMode, LessonType
from bot.repositories import (
    StudentRepository, GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, TeacherRepository, UserRepository, StudentRequestRepository,
    LessonRepository,
)
from bot.states import TeacherGroupAddStudentStates
from bot.handlers.common import show_card
from bot.utils.attendees import attendee_ids

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_groups")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _owns_group(teacher_id: str, group_id: str, tg_repo: TeacherGroupRepository) -> bool:
    gids = set(await tg_repo.get_groups_for_teacher(teacher_id))
    return group_id in gids


def _normalize(q: str) -> str:
    return " ".join((q or "").strip().split()).lower()


# ─── Список групп педагога ───────────────────────────────────────────────────

@router.callback_query(F.data == "teacher:my_groups")
async def cb_my_groups(
    callback: CallbackQuery,
    user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    gids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    groups = [g for g in await group_repo.get_all() if g.group_id in gids]

    if not groups:
        await callback.message.edit_text(
            "У вас нет тренировочных групп. Обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
        return

    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups.sort(key=lambda g: (branches.get(g.branch_id, ""), g.name))

    rows = [
        [InlineKeyboardButton(
            text=f"🏢 {branches.get(g.branch_id, '—')} / {g.name}",
            callback_data=f"t_group_card:{g.group_id}",
        )]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])

    await callback.message.edit_text(
        f"Ваши группы ({len(groups)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Карточка группы ────────────────────────────────────────────────────────

_MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _kb_t_group_card(group, students: list) -> InlineKeyboardMarkup:
    group_id = group.group_id
    rows = [
        [InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"t_student_card:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить ученика", callback_data=f"t_grp_add:{group_id}")])
    if students:
        rows.append([InlineKeyboardButton(text="➖ Убрать ученика", callback_data=f"t_grp_rm:{group_id}")])
    if group.billing_mode == GroupBillingMode.PER_VISIT:
        rows.append([InlineKeyboardButton(text="📊 Посещаемость", callback_data=f"t_grp_attendance:{group_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_groups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_t_group_card(
    message: Message, group_id: str,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    group = await group_repo.get_by_id(group_id)
    if not group:
        await message.edit_text("Группа не найдена")
        return
    branch = await branch_repo.get_by_id(group.branch_id)
    branch_name = branch.name if branch else "—"
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    text = (
        f"<b>🏢 {branch_name} / {group.name}</b>\n\n"
        f"Учеников: {len(members)}"
    )
    await show_card(
        message, text,
        reply_markup=_kb_t_group_card(group, members),
    )


@router.callback_query(F.data.startswith("t_group_card:"))
async def cb_t_group_card(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    branch_repo: BranchRepository,
    student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    gid = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, gid, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    await _render_t_group_card(
        callback.message, gid, group_repo, branch_repo, student_repo, student_group_repo,
    )
    await callback.answer()


# ─── Добавить ученика (поиск или создать через заявку) ──────────────────────

def _kb_t_add_results(
    group_id: str, found: list, member_ids: set[str], query: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in found[:20]:
        mark = " ✓" if s.student_id in member_ids else ""
        rows.append([InlineKeyboardButton(
            text=f"{s.name}{mark}",
            callback_data=f"t_grp_pick:{group_id}:{s.student_id}",
        )])
    parts = (query or "").strip().split()
    if len(parts) == 2:
        rows.append([InlineKeyboardButton(
            text=f"✨ Создать нового «{' '.join(parts)}» (заявка админу)",
            callback_data=f"t_grp_new:{group_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("t_grp_add:"))
async def cb_t_grp_add(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    group_repo: GroupRepository, teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    await state.set_state(TeacherGroupAddStudentStates.searching)
    await state.update_data(group_id=group_id)
    await callback.message.edit_text(
        f"<b>➕ Добавить ученика в «{group.name if group else group_id}»</b>\n\n"
        "Введите <b>Фамилию Имя</b> (или часть). Бот найдёт совпадения среди учеников школы.\n"
        "Если нужного ученика нет — можно создать нового (уйдёт как заявка админу).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")],
        ]),
    )
    await callback.answer()


@router.message(TeacherGroupAddStudentStates.searching)
async def msg_t_grp_add_search(
    message: Message, state: FSMContext, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        return
    data = await state.get_data()
    group_id = str(data.get("group_id") or "")
    if not group_id:
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("❗ Введите ФИО или часть.")
        return
    await state.update_data(query=query)

    q_norm = _normalize(query)
    all_students = await student_repo.get_all()
    found = sorted(
        [s for s in all_students if q_norm in s.name.lower()],
        key=lambda s: s.name,
    )
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    group = await group_repo.get_by_id(group_id)
    header = f"Группа: <b>{group.name if group else group_id}</b>"

    if not found:
        parts = query.split()
        lines = [header, "", f"По запросу «{query}» ничего не найдено."]
        if len(parts) == 2:
            lines.append("")
            lines.append("Можно создать нового ученика (заявка админу).")
        else:
            lines.append("")
            lines.append("Для создания нового введите ровно <b>Фамилию и Имя</b>.")
        await message.answer(
            "\n".join(lines),
            reply_markup=_kb_t_add_results(group_id, [], member_ids, query),
        )
        return

    lines = [header, "", f"Найдено: <b>{len(found)}</b>. ✓ — уже в этой группе."]
    await message.answer(
        "\n".join(lines),
        reply_markup=_kb_t_add_results(group_id, found, member_ids, query),
    )


@router.callback_query(F.data.startswith("t_grp_pick:"), TeacherGroupAddStudentStates.searching)
async def cb_t_grp_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_group_repo: StudentGroupRepository,
    student_repo: StudentRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    if await student_group_repo.exists(student_id, group_id):
        await callback.answer(f"{student.name} уже в этой группе", show_alert=True)
    else:
        await student_group_repo.add(student_id, group_id)
        await callback.answer(f"✅ {student.name} добавлен(а)")
    await state.clear()
    await _render_t_group_card(
        callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
    )


@router.callback_query(F.data.startswith("t_grp_new:"), TeacherGroupAddStudentStates.searching)
async def cb_t_grp_new(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository, user_repo: UserRepository,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    data = await state.get_data()
    query = str(data.get("query") or "").strip()
    parts = query.split()
    if len(parts) != 2:
        await callback.answer("Нужно ровно Фамилию и Имя", show_alert=True)
        return
    student_name = " ".join(parts)

    # Shortcut: педагог-админ создаёт напрямую.
    if user.is_admin:
        student = await student_repo.add(name=student_name)
        await student_group_repo.add(student.student_id, group_id)
        await state.clear()
        await callback.answer(f"✅ Создан: {student.name}")
        await _render_t_group_card(
            callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
        )
        return

    # Заявка админу.
    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    if not admins:
        await callback.answer("В системе нет администратора.", show_alert=True)
        return

    group = await group_repo.get_by_id(group_id)
    gname = group.name if group else group_id
    branch = await branch_repo.get_by_id(group.branch_id) if group else None
    bname = branch.name if branch else "—"

    req_id = uuid.uuid4().hex[:8]
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать и привязать", callback_data=f"req_approve:{req_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req_reject:{req_id}")],
    ])
    notify_text = (
        f"📝 <b>Заявка на создание ученика</b>\n\n"
        f"Педагог: <b>{teacher_name}</b>\n"
        f"Фамилия Имя: <b>{student_name}</b>\n"
        f"Группа: <b>{gname}</b> (филиал «{bname}»)"
    )
    admin_msgs: list[tuple[int, int]] = []
    for admin in admins:
        try:
            msg = await callback.bot.send_message(admin.tg_id, notify_text, reply_markup=admin_kb)
            admin_msgs.append((msg.chat.id, msg.message_id))
        except Exception:
            pass

    try:
        await student_request_repo.add(
            request_id=req_id,
            teacher_id=user.teacher_id,
            teacher_tg_id=callback.from_user.id,
            teacher_name=teacher_name,
            student_name=student_name,
            group_id=group_id,
            admin_msgs=admin_msgs,
        )
    except Exception as exc:
        logger.error("Не удалось сохранить заявку в Sheets: %s", exc)
        await callback.answer("Не удалось сохранить заявку. Попробуйте позже.", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>Заявка отправлена администратору</b>\n\n"
        f"Ученик: <b>{student_name}</b>\n"
        f"Группа: <b>{gname}</b> (филиал «{bname}»)\n\n"
        "Вы получите уведомление после обработки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к группе", callback_data=f"t_group_card:{group_id}")],
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


# ─── Убрать ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("t_grp_rm:"))
async def cb_t_grp_rm(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not members:
        await callback.answer("В группе нет учеников", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=f"➖ {s.name}", callback_data=f"t_grp_rm_do:{group_id}:{s.student_id}")]
        for s in members
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")])
    await callback.message.edit_text(
        f"<b>➖ Убрать из «{group.name}»</b>\n\nВыберите ученика — он будет убран только из этой группы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_grp_rm_do:"))
async def cb_t_grp_rm_do(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    removed = await student_group_repo.remove(student_id, group_id)
    if removed and student:
        await callback.answer(f"✅ {student.name} убран(а) из группы")
    else:
        await callback.answer("Не удалось убрать")
    await _render_t_group_card(
        callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
    )


# ─── Посещаемость группы ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("t_grp_attendance:"))
async def cb_t_grp_attendance(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    today = date.today()
    months = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(3)]
    rows = []
    for ym in months:
        y, m = ym.split("-")
        rows.append([InlineKeyboardButton(
            text=f"{_MONTHS_RU[int(m)]} {y}",
            callback_data=f"t_grp_att_m:{group_id}:{ym}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_group_card:{group_id}")])

    await callback.message.edit_text(
        f"<b>📊 Посещаемость — {group.name}</b>\n\nВыберите месяц:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_grp_att_m:"))
async def cb_t_grp_att_month(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, period_month = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    all_students = await student_repo.get_all()
    members = {s.student_id: s.name for s in all_students if s.student_id in member_ids}

    all_lessons = await lesson_repo.get_by_teacher_and_period(user.teacher_id, period_month)
    group_lessons = sorted(
        [ls for ls in all_lessons if ls.type == LessonType.GROUP and ls.group_id == group_id],
        key=lambda ls: ls.date,
    )

    attendance: dict[str, list[str]] = defaultdict(list)
    for ls in group_lessons:
        for sid in attendee_ids(ls.attendees):
            if sid in members:
                attendance[sid].append(ls.date)

    y, m = period_month.split("-")
    month_label = f"{_MONTHS_RU[int(m)]} {y}"

    lines = [
        f"<b>📊 {group.name}</b>",
        f"{month_label} · занятий: {len(group_lessons)}",
        "",
    ]

    sorted_ids = sorted(members.keys(), key=lambda sid: -len(attendance.get(sid, [])))
    for sid in sorted_ids:
        name = members[sid]
        dates = attendance.get(sid, [])
        if dates:
            dates_str = ", ".join(d[8:10] + "." + d[5:7] for d in dates)
            lines.append(f"<b>{name}</b> — {len(dates)}")
            lines.append(f"  {dates_str}")
        else:
            lines.append(f"<b>{name}</b> — 0")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=f"t_grp_attendance:{group_id}")],
        ]),
    )
    await callback.answer()
