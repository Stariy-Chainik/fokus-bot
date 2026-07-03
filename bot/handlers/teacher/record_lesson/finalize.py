from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode, StudentGroupTier
from bot.models.enums import LessonType
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, UserRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_lesson_type_after_save, kb_duration, kb_teacher_menu,
    kb_attendance_yes_no, kb_pair_multi_select, kb_pair_from_soloists,
    kb_multi_select, kb_group_roster_per_visit,
    kb_other_groups_picker, kb_other_group_students,
    kb_group_branch_picker, kb_group_picker, kb_shared_group_picker,
    _PROXY_BUTTONS,
)
from bot.keyboards.admin import kb_admin_menu
from bot.utils import build_group_attendees_csv
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display
from bot.utils.locks import InProgressGuard

from ._base import _tid, _menu_kb
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)

_confirming_lesson_ids = InProgressGuard()


async def _reset_for_next_lesson(state: FSMContext, lesson_date: str, data: dict) -> None:
    """Цикл «ещё занятие с той же датой»: чистим выбор, оставляем дату и proxy."""
    await state.set_data({"lesson_date": lesson_date, "proxy_teacher_id": data.get("proxy_teacher_id")})
    await state.set_state(RecordLessonStates.choosing_kind)


async def _finalize_group(
    callback: CallbackQuery, state: FSMContext, data: dict, teacher,
    lesson_date: str, duration: int, bypass_lock: bool, after_save_kb,
    lesson_service: LessonService, group_repo: GroupRepository | None,
) -> bool:
    attendee_ids = list(data.get("selected_ids", []))
    group_id = data.get("selected_group_id") or ""
    tiers = data.get("per_visit_tiers") or {}
    group = None
    if group_id and group_repo is not None:
        group = await group_repo.get_by_id(group_id)
    attendees_csv = build_group_attendees_csv(group, attendee_ids, tiers)
    lesson = await lesson_service.create(
        teacher=teacher,
        lesson_type=LessonType.GROUP,
        lesson_date=lesson_date,
        duration_min=duration,
        attendees=attendees_csv,
        group_id=group_id,
        bypass_period_lock=bypass_lock,
    )
    extra = f"\nОтмечено: {len(attendee_ids)}" if attendee_ids else ""
    await _reset_for_next_lesson(state, lesson_date, data)
    await callback.message.edit_text(
        f"<b>✅ Групповое занятие записано</b>\nID: {lesson.lesson_id}\n"
        f"Дата: {format_date_display(lesson.date)}{extra}\n\n"
        f"Продолжим? Выберите тип следующего занятия:",
        reply_markup=after_save_kb,
    )
    return True


async def _finalize_pair(
    callback: CallbackQuery, state: FSMContext, data: dict, teacher,
    lesson_date: str, duration: int, bypass_lock: bool, after_save_kb,
    lesson_service: LessonService, student_repo: StudentRepository,
) -> bool:
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
        bypass_period_lock=bypass_lock,
    )
    await _reset_for_next_lesson(state, lesson_date, data)
    await callback.message.edit_text(
        f"<b>✅ Создано парных занятий: {len(lessons)}</b>\n"
        f"Дата: {format_date_display(lesson_date)}\n"
        f"Пары: {'; '.join(pair_labels)}\n\n"
        f"Продолжим? Выберите тип следующего занятия:",
        reply_markup=after_save_kb,
    )
    return True


async def _finalize_shared(
    callback: CallbackQuery, state: FSMContext, data: dict, teacher,
    lesson_date: str, duration: int, bypass_lock: bool, after_save_kb,
    lesson_service: LessonService, student_repo: StudentRepository,
) -> bool:
    keys = list(data.get("selected_ids", []))
    if not (2 <= len(keys) <= 4):
        await callback.answer("Нужно от 2 до 4 солистов", show_alert=True)
        return False
    students = []
    for sid in keys:
        st = await student_repo.get_by_id(sid)
        if st:
            students.append(st)
    if not students:
        await callback.answer("Ученики не найдены", show_alert=True)
        return False
    students.sort(key=lambda s: s.student_id)
    lesson = await lesson_service.create_shared_individual(
        teacher=teacher,
        lesson_date=lesson_date,
        duration_min=duration,
        students=[(s.student_id, s.name) for s in students],
        bypass_period_lock=bypass_lock,
    )
    label = " + ".join(s.name for s in students)
    await _reset_for_next_lesson(state, lesson_date, data)
    await callback.message.edit_text(
        f"<b>✅ Индивидуальное занятие записано</b>\n"
        f"ID: {lesson.lesson_id}\n"
        f"Дата: {format_date_display(lesson_date)}\n"
        f"Ученики ({len(students)}): {label}\n\n"
        f"Продолжим? Выберите тип следующего занятия:",
        reply_markup=after_save_kb,
    )
    return True


async def _finalize_soloist(
    callback: CallbackQuery, state: FSMContext, data: dict, teacher,
    lesson_date: str, duration: int, bypass_lock: bool, after_save_kb,
    lesson_service: LessonService, student_repo: StudentRepository,
) -> bool:
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
        bypass_period_lock=bypass_lock,
    )
    names = ", ".join(n for _, n in students)
    await _reset_for_next_lesson(state, lesson_date, data)
    await callback.message.edit_text(
        f"<b>✅ Создано соло-занятий: {len(lessons)}</b>\n"
        f"Дата: {format_date_display(lesson_date)}\n"
        f"Ученики: {names}\n\n"
        f"Продолжим? Выберите тип следующего занятия:",
        reply_markup=after_save_kb,
    )
    return True


async def _finalize(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, student_repo: StudentRepository | None,
    lesson_service: LessonService,
    group_repo: GroupRepository | None = None,
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
        teacher = await teacher_repo.get_by_id(_tid(user, data))
        if not teacher:
            await state.clear()
            await callback.message.edit_text("Педагог не найден. Обратитесь к администратору.")
            return

        kind = data.get("kind")
        lesson_date = data["lesson_date"]
        duration = int(data["duration_min"])
        _is_admin_mode = user and user.is_admin and (not user.teacher_id or data.get("proxy_teacher_id"))
        after_save_kb = kb_lesson_type_after_save(back_cb="admin:menu" if _is_admin_mode else "teacher:menu")
        bypass_lock = bool(user and user.is_admin)

        branch_args = (
            callback, state, data, teacher,
            lesson_date, duration, bypass_lock, after_save_kb, lesson_service,
        )
        if kind == "group":
            done = await _finalize_group(*branch_args, group_repo)
        elif kind == "pair":
            done = await _finalize_pair(*branch_args, student_repo)
        elif kind == "shared":
            done = await _finalize_shared(*branch_args, student_repo)
        elif kind == "soloist":
            done = await _finalize_soloist(*branch_args, student_repo)
        else:
            await state.clear()
            await callback.message.edit_text(
                "Неизвестный тип занятия.", reply_markup=_menu_kb(user, data),
            )
            done = True
        if not done:
            # Ранний выход ветки (alert уже показан) — как прежний return из try:
            # финальный callback.answer() пропускается.
            return
    except PermissionError as exc:
        await state.clear()
        await callback.message.edit_text(
            f"🔒 {exc}\nОбратитесь к администратору.", reply_markup=_menu_kb(user, data),
        )
    except ValueError as exc:
        await state.clear()
        await callback.message.edit_text(f"Ошибка: {exc}", reply_markup=_menu_kb(user, data))
    except Exception as exc:
        logger.error("Ошибка записи занятия: %s", exc)
        await state.clear()
        await callback.message.edit_text(
            "Ошибка при сохранении занятия. Попробуйте позже.", reply_markup=_menu_kb(user, data),
        )
    finally:
        _confirming_lesson_ids.discard(lock_key)

    await callback.answer()
