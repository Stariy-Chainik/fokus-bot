from __future__ import annotations
import logging
from datetime import date, datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import (
    LessonRepository, StudentRepository, TeacherStudentRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.services import LessonService
from bot.states import EditLessonStates
from bot.keyboards.teacher import kb_teacher_menu
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="teacher_edit_lesson")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _period_locked(
    teacher_id: str, period_month: str,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> bool:
    sub = await submission_repo.get_by_teacher_and_period(teacher_id, period_month)
    return sub is not None


async def _linked_students(teacher_id: str, ts_repo, student_repo):
    student_ids = await ts_repo.get_students_for_teacher(teacher_id)
    all_students = await student_repo.get_all()
    linked = [s for s in all_students if s.student_id in student_ids]
    return sorted(linked, key=lambda s: s.name)


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


def _cancel_row(lesson_id: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="« Отмена", callback_data=f"lesson_detail:{lesson_id}")]


def _kb_edit_duration(lesson_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="45 мин", callback_data=f"edit_dur_pick:{lesson_id}:45"),
            InlineKeyboardButton(text="60 мин", callback_data=f"edit_dur_pick:{lesson_id}:60"),
            InlineKeyboardButton(text="90 мин", callback_data=f"edit_dur_pick:{lesson_id}:90"),
        ],
        _cancel_row(lesson_id),
    ])


def _kb_edit_attendees(lesson_id: str, students, selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in students:
        mark = "✅" if s.student_id in selected else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {s.name}", callback_data=f"edit_att_toggle:{s.student_id}",
        )])
    rows.append([InlineKeyboardButton(
        text=f"✅ Сохранить ({len(selected)})", callback_data="edit_att_confirm",
    )])
    rows.append(_cancel_row(lesson_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_edit_soloist(lesson_id: str, students) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"edit_solo_pick:{lesson_id}:{s.student_id}")]
        for s in students
    ]
    rows.append(_cancel_row(lesson_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_edit_pair(lesson_id: str, pairs) -> InlineKeyboardMarkup:
    rows = []
    for a, b in pairs:
        rows.append([InlineKeyboardButton(
            text=f"{a.name} ↔ {b.name}",
            callback_data=f"edit_pair_pick:{lesson_id}:{a.student_id}",
        )])
    rows.append(_cancel_row(lesson_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _guard_access(
    callback: CallbackQuery, user: User | None, lesson_id: str,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
):
    """Возвращает (lesson) если доступ ок, иначе None (и отвечает alert-ом)."""
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return None
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or lesson.teacher_id != user.teacher_id:
        await callback.answer("Занятие не найдено", show_alert=True)
        return None
    if await _period_locked(user.teacher_id, lesson.date[:7], submission_repo):
        await callback.answer("🔒 Период сдан — обратитесь к администратору.", show_alert=True)
        return None
    return lesson


# ─── Вход: edit_lesson:<field>:<lesson_id> ──────────────────────────────────

@router.callback_query(F.data.startswith("edit_lesson:"))
async def cb_edit_entry(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    _, field, lesson_id = parts

    lesson = await _guard_access(callback, user, lesson_id, lesson_repo, submission_repo)
    if lesson is None:
        return

    await state.clear()
    await state.update_data(lesson_id=lesson_id)

    if field == "date":
        await state.set_state(EditLessonStates.editing_date)
        await callback.message.edit_text(
            f"Занятие {lesson_id}. Текущая дата: {format_date_display(lesson.date)}.\n"
            "Введите новую дату в формате ДД.ММ.ГГГГ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_cancel_row(lesson_id)]),
        )

    elif field == "duration":
        await state.set_state(EditLessonStates.editing_duration)
        await callback.message.edit_text(
            f"Занятие {lesson_id}. Текущая длительность: {lesson.duration_min} мин.\n"
            "Выберите новую:",
            reply_markup=_kb_edit_duration(lesson_id),
        )

    elif field == "attendees":
        if lesson.type != LessonType.GROUP:
            await callback.answer("Присутствующие только для групповых занятий.", show_alert=True)
            return
        mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
        if not mine:
            await callback.answer("Нет учеников в списке.", show_alert=True)
            return
        selected = set((lesson.attendees or "").split(",")) if lesson.attendees else set()
        selected.discard("")
        await state.set_state(EditLessonStates.editing_attendees)
        await state.update_data(selected_ids=list(selected))
        await callback.message.edit_text(
            f"Занятие {lesson_id}. Отметьте присутствующих:",
            reply_markup=_kb_edit_attendees(lesson_id, mine, selected),
        )

    elif field == "soloist":
        if lesson.type != LessonType.INDIVIDUAL or lesson.student_2_id:
            await callback.answer("Это не соло-занятие.", show_alert=True)
            return
        mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
        if not mine:
            await callback.answer("Нет учеников в списке.", show_alert=True)
            return
        await state.set_state(EditLessonStates.editing_student_soloist)
        await callback.message.edit_text(
            f"Занятие {lesson_id}. Выберите нового ученика:",
            reply_markup=_kb_edit_soloist(lesson_id, mine),
        )

    elif field == "pair":
        if lesson.type != LessonType.INDIVIDUAL or not lesson.student_2_id:
            await callback.answer("Это не парное занятие.", show_alert=True)
            return
        pairs = await _collect_pairs(user.teacher_id, ts_repo, student_repo)
        if not pairs:
            await callback.answer("Нет пар среди ваших учеников.", show_alert=True)
            return
        await state.set_state(EditLessonStates.editing_pair)
        await callback.message.edit_text(
            f"Занятие {lesson_id}. Выберите новую пару:",
            reply_markup=_kb_edit_pair(lesson_id, pairs),
        )
    else:
        await callback.answer("Неизвестное поле", show_alert=True)
        return

    await callback.answer()


# ─── Длительность ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_dur_pick:"), EditLessonStates.editing_duration)
async def cb_edit_duration_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, lesson_id, dur = callback.data.split(":")
    try:
        await lesson_service.update_and_rebill(lesson_id, duration_min=int(dur))
    except PermissionError:
        await callback.answer("🔒 Период сдан.", show_alert=True)
        return
    except Exception as exc:
        logger.error("Edit duration failed: %s", exc)
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"✅ Длительность обновлена: {dur} мин.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()


# ─── Дата ──────────────────────────────────────────────────────────────────

@router.message(EditLessonStates.editing_date)
async def msg_edit_date(
    message: Message, user: User | None, state: FSMContext,
    lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        return
    data = await state.get_data()
    lesson_id = data.get("lesson_id")
    if not lesson_id:
        await message.answer("Контекст утерян. Откройте занятие заново.")
        await state.clear()
        return
    text = (message.text or "").strip()
    new_date = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if d > date.today():
                await message.answer("Дата в будущем запрещена. Введите другую:")
                return
            new_date = d.isoformat()
            break
        except ValueError:
            pass
    if new_date is None:
        await message.answer("Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:")
        return
    try:
        await lesson_service.update_and_rebill(lesson_id, date=new_date)
    except PermissionError as exc:
        await state.clear()
        await message.answer(f"🔒 {exc}", reply_markup=kb_teacher_menu())
        return
    except ValueError as exc:
        await message.answer(f"Ошибка: {exc}")
        return
    except Exception as exc:
        logger.error("Edit date failed: %s", exc)
        await message.answer("Ошибка. Попробуйте позже.", reply_markup=kb_teacher_menu())
        await state.clear()
        return
    await state.clear()
    await message.answer(
        f"✅ Дата обновлена: {format_date_display(new_date)}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )


# ─── Attendees (group) ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_att_toggle:"), EditLessonStates.editing_attendees)
async def cb_edit_att_toggle(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    lesson_id = data.get("lesson_id")
    selected = list(data.get("selected_ids", []))
    if student_id in selected:
        selected.remove(student_id)
    else:
        selected.append(student_id)
    await state.update_data(selected_ids=selected)

    mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
    await callback.message.edit_reply_markup(
        reply_markup=_kb_edit_attendees(lesson_id, mine, set(selected)),
    )
    await callback.answer()


@router.callback_query(F.data == "edit_att_confirm", EditLessonStates.editing_attendees)
async def cb_edit_att_confirm(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    lesson_id = data.get("lesson_id")
    selected = list(data.get("selected_ids", []))
    attendees_csv = ",".join(selected) if selected else None
    try:
        await lesson_service.update_and_rebill(lesson_id, attendees=attendees_csv)
    except PermissionError:
        await callback.answer("🔒 Период сдан.", show_alert=True)
        return
    except Exception as exc:
        logger.error("Edit attendees failed: %s", exc)
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"✅ Присутствующие обновлены ({len(selected)}).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()


# ─── Soloist (individual, 1 ученик) ────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_solo_pick:"), EditLessonStates.editing_student_soloist)
async def cb_edit_solo_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, lesson_id, student_id = callback.data.split(":")
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    try:
        await lesson_service.update_and_rebill(
            lesson_id,
            student_1_id=student.student_id,
            student_1_name=student.name,
        )
    except PermissionError:
        await callback.answer("🔒 Период сдан.", show_alert=True)
        return
    except Exception as exc:
        logger.error("Edit soloist failed: %s", exc)
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"✅ Ученик обновлён: {student.name}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()


# ─── Pair (individual, 2 ученика) ──────────────────────────────────────────

@router.callback_query(F.data.startswith("edit_pair_pick:"), EditLessonStates.editing_pair)
async def cb_edit_pair_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, lesson_id, a_id = callback.data.split(":")
    a = await student_repo.get_by_id(a_id)
    if not a or not a.partner_id:
        await callback.answer("Пара не найдена", show_alert=True)
        return
    b = await student_repo.get_by_id(a.partner_id)
    if not b:
        await callback.answer("Партнёр не найден", show_alert=True)
        return
    try:
        await lesson_service.update_and_rebill(
            lesson_id,
            student_1_id=a.student_id, student_1_name=a.name,
            student_2_id=b.student_id, student_2_name=b.name,
        )
    except PermissionError:
        await callback.answer("🔒 Период сдан.", show_alert=True)
        return
    except Exception as exc:
        logger.error("Edit pair failed: %s", exc)
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        f"✅ Пара обновлена: {a.name} ↔ {b.name}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К занятию", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()
