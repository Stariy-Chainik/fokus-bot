from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from bot.models.enums import LessonType
from bot.repositories import (
    LessonRepository, TeacherPeriodSubmissionRepository, GroupRepository,
    StudentRepository, TeacherGroupRepository, StudentGroupRepository,
)
from bot.services import LessonService
from bot.keyboards.teacher import kb_lesson_list, kb_lesson_detail, kb_teacher_menu
from bot.keyboards.admin import kb_back
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display
from bot.utils import parse_attendees, serialize_attendees, AttendeeEntry, attendee_ids
from bot.handlers.common import show_card

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_lessons")
PAGE_SIZE = 20

_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


def _is_teacher_or_admin(user: User | None) -> bool:
    return user is not None and (user.teacher_id is not None or user.is_admin)


def _can_view_lesson(user: User | None, lesson) -> bool:
    if user is None:
        return False
    if user.is_admin:
        return True
    return user.teacher_id is not None and lesson.teacher_id == user.teacher_id


def _month_label(ym: str) -> str:
    y, m = ym.split("-")
    return f"{_MONTHS_RU[int(m) - 1]} {y}"


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _date_filter_kb() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"my_lessons_date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"my_lessons_date:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="my_lessons_date:manual")],
        [InlineKeyboardButton(text="📋 За месяц", callback_data="my_lessons_date:month")],
        [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_lessons")],
    ])


def _month_picker_kb() -> InlineKeyboardMarkup:
    today = date.today()
    cur_y, cur_m = today.year, today.month
    prev_y, prev_m = _shift_month(cur_y, cur_m, -1)
    cur_ym = f"{cur_y:04d}-{cur_m:02d}"
    prev_ym = f"{prev_y:04d}-{prev_m:02d}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_month_label(cur_ym), callback_data=f"my_lessons_month:{cur_ym}")],
        [InlineKeyboardButton(text=_month_label(prev_ym), callback_data=f"my_lessons_month:{prev_ym}")],
        [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_lessons")],
    ])


async def _submitted_periods(teacher_id: str, submission_repo: TeacherPeriodSubmissionRepository) -> set[str]:
    subs = await submission_repo.get_by_teacher(teacher_id)
    return {s.period_month for s in subs}


def _locked_ids(lessons, submitted_periods: set[str]) -> set[str]:
    return {ls.lesson_id for ls in lessons if ls.date[:7] in submitted_periods}


async def _get_mode(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("lm_mode", "view")


async def _show_lessons(
    callback: CallbackQuery, user: User, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    state: FSMContext,
    filter_date: str | None = None, filter_month: str | None = None,
    filter_type: str | None = None,
) -> None:
    mode = await _get_mode(state)
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    if filter_date:
        lessons = [ls for ls in lessons if ls.date == filter_date]
    elif filter_month:
        lessons = [ls for ls in lessons if ls.date[:7] == filter_month]

    periods = await _submitted_periods(user.teacher_id, submission_repo)
    if mode == "delete":
        lessons = [ls for ls in lessons if ls.date[:7] not in periods]

    if filter_type == "group":
        lessons = [ls for ls in lessons if ls.type == LessonType.GROUP]
    elif filter_type == "individual":
        lessons = [ls for ls in lessons if ls.type == LessonType.INDIVIDUAL]

    lessons.sort(key=lambda ls: ls.date, reverse=True)

    if filter_date:
        filter_tag = filter_date
    elif filter_month:
        filter_tag = f"m-{filter_month}"
    else:
        filter_tag = "all"
    await state.update_data(lm_filter_tag=filter_tag, lm_filter_type=filter_type)

    if filter_date:
        title_frag = f"за {format_date_display(filter_date)}"
    elif filter_month:
        title_frag = f"за {_month_label(filter_month)}"
    else:
        title_frag = ""
    type_frag = {
        "group": " · групповые",
        "individual": " · индивидуальные",
    }.get(filter_type or "", "")

    locked = _locked_ids(lessons, periods)
    if not lessons:
        extra = " (или все в сданных периодах)" if mode == "delete" else ""
        # Всё равно показываем клавиатуру списка — чтобы можно было переключить тип-фильтр.
        await callback.message.edit_text(
            f"Занятий {title_frag}{type_frag} не найдено{extra}.",
            reply_markup=kb_lesson_list(
                [], page=0, page_size=PAGE_SIZE,
                locked_ids=locked, filter_date=filter_date, filter_month=filter_month,
                filter_type=filter_type,
            ),
        )
        return

    header = f"<b>Занятия {title_frag}{type_frag}</b> ({len(lessons)}):" if title_frag else f"<b>Занятия</b>{type_frag} ({len(lessons)}):"
    await callback.message.edit_text(
        header,
        reply_markup=kb_lesson_list(
            lessons, page=0, page_size=PAGE_SIZE,
            locked_ids=locked, filter_date=filter_date, filter_month=filter_month,
            filter_type=filter_type,
        ),
    )


@router.callback_query(F.data == "teacher:my_lessons")
async def cb_my_lessons(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "<b>Мои занятия</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить занятие", callback_data="teacher:record_lesson")],
            [InlineKeyboardButton(text="👁 Посмотреть занятия", callback_data="teacher:lesson_view")],
            [InlineKeyboardButton(text="🗑 Удалить занятие", callback_data="teacher:lesson_delete")],
            [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:lesson_view")
async def cb_lesson_view(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.update_data(lm_mode="view")
    await callback.message.edit_text(
        "<b>Посмотреть занятия</b>\nЗа какую дату показать?",
        reply_markup=_date_filter_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:lesson_delete")
async def cb_lesson_delete(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.update_data(lm_mode="delete")
    await callback.message.edit_text(
        "<b>Удалить занятие</b>\n"
        "За какую дату показать?\n"
        "(нажмите на занятие в списке, чтобы удалить)",
        reply_markup=_date_filter_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("my_lessons_date:"))
async def cb_my_lessons_date(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    value = callback.data.split(":", 1)[1]
    if value == "manual":
        today = date.today()
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=kb_calendar(
                today.year, today.month, prefix="lv",
                cancel_cb="teacher:my_lessons",
            ),
        )
        await callback.answer()
        return
    if value == "month":
        await callback.message.edit_text(
            "Выберите месяц:", reply_markup=_month_picker_kb(),
        )
        await callback.answer()
        return
    await _show_lessons(callback, user, lesson_repo, submission_repo, state, filter_date=value)
    await callback.answer()


@router.callback_query(F.data.startswith("my_lessons_month:"))
async def cb_my_lessons_month(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    await _show_lessons(callback, user, lesson_repo, submission_repo, state, filter_month=ym)
    await callback.answer()


@router.callback_query(F.data.startswith("lv_nav:"))
async def cb_lv_nav(callback: CallbackQuery, user: User | None) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="lv", cancel_cb="teacher:my_lessons"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lv_pick:"))
async def cb_lv_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    filter_date = callback.data.split(":", 1)[1]
    await _show_lessons(callback, user, lesson_repo, submission_repo, state, filter_date=filter_date)
    await callback.answer()


def _parse_type_code(code: str) -> str | None:
    return {"g": "group", "i": "individual"}.get(code)


def _parse_filter_tag(tag: str) -> tuple[str | None, str | None]:
    if tag.startswith("m-"):
        return None, tag[2:]
    if tag == "all":
        return None, None
    return tag, None


@router.callback_query(F.data.startswith("lessons_page:"))
async def cb_lessons_page(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[1])
    tag = parts[2] if len(parts) > 2 else "all"
    type_code = parts[3] if len(parts) > 3 else "a"
    filter_date, filter_month = _parse_filter_tag(tag)
    filter_type = _parse_type_code(type_code)

    mode = await _get_mode(state)
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    if filter_date:
        lessons = [ls for ls in lessons if ls.date == filter_date]
    elif filter_month:
        lessons = [ls for ls in lessons if ls.date[:7] == filter_month]

    periods = await _submitted_periods(user.teacher_id, submission_repo)
    if mode == "delete":
        lessons = [ls for ls in lessons if ls.date[:7] not in periods]

    if filter_type == "group":
        lessons = [ls for ls in lessons if ls.type == LessonType.GROUP]
    elif filter_type == "individual":
        lessons = [ls for ls in lessons if ls.type == LessonType.INDIVIDUAL]

    lessons.sort(key=lambda ls: ls.date, reverse=True)
    locked = _locked_ids(lessons, periods)
    await callback.message.edit_reply_markup(
        reply_markup=kb_lesson_list(
            lessons, page=page, page_size=PAGE_SIZE,
            locked_ids=locked, filter_date=filter_date, filter_month=filter_month,
            filter_type=filter_type,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lessons_type:"))
async def cb_lessons_type(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, type_code, tag = callback.data.split(":", 2)
    filter_type = _parse_type_code(type_code)
    filter_date, filter_month = _parse_filter_tag(tag)
    await _show_lessons(
        callback, user, lesson_repo, submission_repo, state,
        filter_date=filter_date, filter_month=filter_month, filter_type=filter_type,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_detail:"))
async def cb_lesson_detail(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    group_repo: GroupRepository, student_repo: StudentRepository,
    state: FSMContext,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or not _can_view_lesson(user, lesson):
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    periods = await _submitted_periods(lesson.teacher_id, submission_repo)
    locked = lesson.date[:7] in periods

    lines = [
        f"<b>Занятие {lesson.lesson_id}</b>",
        f"Дата: {format_date_display(lesson.date)}",
        f"Тип: {'Групповое' if lesson.type == LessonType.GROUP else 'Индивидуальное'}",
        f"Длительность: {lesson.duration_min} мин",
    ]
    if lesson.group_id:
        group = await group_repo.get_by_id(lesson.group_id)
        if group:
            lines.append(f"Группа: {group.name}")
    if lesson.student_1_name:
        lines.append(f"Ученик 1: {lesson.student_1_name}")
    if lesson.student_2_name:
        lines.append(f"Ученик 2: {lesson.student_2_name}")
    if lesson.student_3_name:
        lines.append(f"Ученик 3: {lesson.student_3_name}")
    if lesson.student_4_name:
        lines.append(f"Ученик 4: {lesson.student_4_name}")

    if lesson.type == LessonType.GROUP and lesson.attendees:
        entries = parse_attendees(lesson.attendees, default_duration=lesson.duration_min)
        if entries:
            lines.append("")
            lines.append(f"<b>Присутствовали ({len(entries)}):</b>")
            total = 0
            for e in entries:
                st = await student_repo.get_by_id(e.student_id)
                name = st.name if st else e.student_id
                if e.amount > 0:
                    lines.append(f"  • {name} · {e.duration_min} мин · {e.amount}₽")
                    total += e.amount
                else:
                    lines.append(f"  • {name} · {e.duration_min} мин · абонемент")
            if total > 0:
                lines.append(f"<b>Итого: {total} ₽</b>")

    if locked:
        lines.append("")
        if user.is_admin:
            lines.append("🔒 Период сдан педагогом — редактирование доступно только администратору.")
        else:
            lines.append("🔒 Период сдан — редактирование недоступно.")

    data = await state.get_data()
    from_teacher_flow = bool(data.get("lm_mode"))
    if from_teacher_flow:
        tag = data.get("lm_filter_tag")
        if tag:
            back_cb = f"lessons_page:0:{tag}"
        else:
            back_cb = "teacher:lesson_view" if data.get("lm_mode") == "view" else "teacher:lesson_delete"
    elif data.get("t_stu_les_back"):
        back_cb = data["t_stu_les_back"]
    else:
        back_cb = "admin:edit_lesson" if user.is_admin else "teacher:lesson_delete"
        logger.info(
            "lesson_detail back fallback: lesson=%s user=%s admin=%s → %s "
            "(no lm_mode / t_stu_les_back in FSM — likely state loss)",
            lesson.lesson_id, callback.from_user.id, user.is_admin, back_cb,
        )
    can_add_guest = (
        (not locked or bool(user.is_admin))
        and lesson.type == LessonType.GROUP
        and bool(lesson.group_id)
    )
    await show_card(callback, "\n".join(lines), reply_markup=kb_lesson_detail(
        lesson, locked, back_cb=back_cb, can_add_guest=can_add_guest,
        is_admin=bool(user.is_admin),
    ))



@router.callback_query(F.data.startswith("delete_lesson:"))
async def cb_delete_lesson_confirm(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or not _can_view_lesson(user, lesson):
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    if not user.is_admin:
        periods = await _submitted_periods(lesson.teacher_id, submission_repo)
        if lesson.date[:7] in periods:
            await callback.answer(
                "🔒 Период сдан — обратитесь к администратору.", show_alert=True,
            )
            return
    await callback.message.edit_text(
        f"Удалить занятие {lesson_id} от {format_date_display(lesson.date)}?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_lesson:{lesson_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_lesson:"))
async def cb_delete_lesson_do(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if lesson and not _can_view_lesson(user, lesson):
        await callback.answer("Нет доступа к этому занятию", show_alert=True)
        return
    try:
        ok = await lesson_service.delete(lesson_id, bypass_period_lock=bool(user.is_admin))
    except PermissionError:
        await callback.answer(
            "🔒 Период сдан — обратитесь к администратору.", show_alert=True,
        )
        return
    text = f"Занятие {lesson_id} удалено." if ok else "Занятие не найдено."
    back_kb = kb_back("admin:edit_lesson") if user.is_admin else kb_teacher_menu()
    await callback.message.edit_text(text, reply_markup=back_kb)
    await callback.answer()


# ─── Добавить гостя в уже сохранённое групповое занятие ──────────────────────

@router.callback_query(F.data.startswith("lesson_guest_list:"))
async def cb_lesson_guest_list(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    # Уже присутствующие
    present_ids = set(attendee_ids(lesson.attendees)) if lesson.attendees else set()

    # Ученики из всех групп педагога
    teacher_id = lesson.teacher_id
    all_gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    all_students: dict[str, object] = {}
    for gid in sorted(all_gids):
        member_ids = set(await student_group_repo.get_students_for_group(gid))
        for s in await student_repo.get_all():
            if s.student_id in member_ids and s.student_id not in present_ids:
                all_students[s.student_id] = s

    candidates = sorted(all_students.values(), key=lambda s: s.name)
    if not candidates:
        await callback.answer("Все ученики уже отмечены.", show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=s.name,
            callback_data=f"lesson_guest_pick:{lesson_id}:{s.student_id}",
        )]
        for s in candidates
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"lesson_detail:{lesson_id}")])
    await callback.message.edit_text(
        f"<b>Выберите ученика</b> (уже отмечено: {len(present_ids)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_guest_pick:"))
async def cb_lesson_guest_pick(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    state: FSMContext,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, lesson_id, student_id = callback.data.split(":", 2)
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    if not user.is_admin:
        periods = await _submitted_periods(lesson.teacher_id, submission_repo)
        if lesson.date[:7] in periods:
            await callback.answer("🔒 Период сдан.", show_alert=True)
            return

    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    # Определяем стоимость по группе
    group = await group_repo.get_by_id(lesson.group_id) if lesson.group_id else None
    if group and group.billing_mode == GroupBillingMode.PER_VISIT:
        amount = group.price_full
    else:
        amount = 0

    new_entry = AttendeeEntry(
        student_id=student_id,
        duration_min=lesson.duration_min,
        amount=amount,
    )

    existing = parse_attendees(lesson.attendees or "", default_duration=lesson.duration_min)
    if any(e.student_id == student_id for e in existing):
        await callback.answer("Этот ученик уже отмечен.", show_alert=True)
        return

    existing.append(new_entry)
    new_attendees = serialize_attendees(existing)
    await lesson_repo.update_attendees(lesson_id, new_attendees)

    await callback.answer(f"✅ {student.name} добавлен(а).")
    # Обновить карточку занятия
    lesson.attendees = new_attendees
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}"),
        ]])
    )
