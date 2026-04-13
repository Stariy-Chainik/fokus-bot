from __future__ import annotations
"""
Педагог: FSM «Отметить занятие».
Порядок: Дата → Тип (группа/пара/соло) → Длительность → ветка → создание.
Группа: опциональная отметка присутствующих. Пара: выбор одной пары.
Соло: мульти-выбор учеников (включая тех, кто в паре — если пришли одни).
Защита от двойного нажатия — set _confirming_lesson_ids по tg_id.
"""
import logging
from datetime import date, timedelta, datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import TeacherRepository, StudentRepository, TeacherStudentRepository
from bot.services import LessonService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_duration, kb_teacher_menu,
    kb_attendance_yes_no, kb_pair_multi_select, kb_multi_select,
)
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
    return " | ".join(parts) + ("\n\n" if parts else "")


@router.callback_query(F.data == "teacher:record_lesson")
async def cb_record_lesson_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(RecordLessonStates.choosing_date)
    await callback.message.edit_text("Выберите дату занятия:", reply_markup=_date_picker_kb())
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
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    target = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if target == "date":
        await state.set_state(RecordLessonStates.choosing_date)
        await callback.message.edit_text("Выберите дату занятия:", reply_markup=_date_picker_kb())

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
        await state.set_state(RecordLessonStates.asking_attendance)
        await callback.message.edit_text(
            f"{_header(data)}Групповое занятие.\nОтметить присутствующих?",
            reply_markup=kb_attendance_yes_no(),
        )

    elif target == "pair":
        await _show_pair_list(callback, state, user, ts_repo, student_repo)

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

@router.callback_query(F.data.startswith("lesson_date:"), RecordLessonStates.choosing_date)
async def cb_lesson_date(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    if value == "manual":
        await callback.message.edit_text("Введите дату в формате ДД.ММ.ГГГГ:")
        await callback.answer()
        return

    if date.fromisoformat(value) > date.today():
        await callback.answer("Дата в будущем запрещена!", show_alert=True)
        return

    await state.update_data(lesson_date=value)
    await state.set_state(RecordLessonStates.choosing_kind)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
    )
    await callback.answer()


@router.message(RecordLessonStates.choosing_date)
async def msg_lesson_date_manual(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lesson_date = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if d > date.today():
                await message.answer("Дата в будущем запрещена. Введите другую дату:")
                return
            lesson_date = d.isoformat()
            break
        except ValueError:
            pass
    if lesson_date is None:
        await message.answer("Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:")
        return
    await state.update_data(lesson_date=lesson_date)
    await state.set_state(RecordLessonStates.choosing_kind)
    data = await state.get_data()
    await message.answer(
        f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
    )


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
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    duration = int(callback.data.split(":", 1)[1])
    await state.update_data(duration_min=duration)
    data = await state.get_data()
    kind = data.get("kind")

    if kind == "group":
        await state.set_state(RecordLessonStates.asking_attendance)
        await callback.message.edit_text(
            f"{_header(data)}Групповое занятие.\nОтметить присутствующих?",
            reply_markup=kb_attendance_yes_no(),
        )

    elif kind == "pair":
        await _show_pair_list(callback, state, user, ts_repo, student_repo)

    elif kind == "soloist":
        mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
        if not mine:
            await callback.message.edit_text(
                "У вас нет учеников.", reply_markup=kb_teacher_menu(),
            )
            await state.clear()
            await callback.answer()
            return
        await state.update_data(selected_ids=[])
        await state.set_state(RecordLessonStates.selecting_soloists)
        await callback.message.edit_text(
            f"{_header(data)}Отметьте учеников для соло ({len(mine)} в списке), затем Подтвердить.\n"
            f"💡 Можно отметить и ученика из пары — если он пришёл один.",
            reply_markup=kb_multi_select(mine, set(), back_cb="lesson_back:duration"),
        )

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
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
    if not mine:
        await callback.message.edit_text(
            "У вас нет учеников.", reply_markup=kb_teacher_menu(),
        )
        await state.clear()
        await callback.answer()
        return
    await state.update_data(selected_ids=[])
    await state.set_state(RecordLessonStates.selecting_attendees)
    data = await state.get_data()
    await callback.message.edit_text(
        f"{_header(data)}Отметьте присутствующих ({len(mine)} в списке):",
        reply_markup=kb_multi_select(mine, set(), back_cb="lesson_back:attendance"),
    )
    await callback.answer()


# ─── Мульти-выбор: переключение и подтверждение ─────────────────────────────

async def _refresh_multi_select(
    callback: CallbackQuery, state: FSMContext, user: User,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    data = await state.get_data()
    selected = set(data.get("selected_ids", []))
    cur_state = await state.get_state()
    mine = await _linked_students(user.teacher_id, ts_repo, student_repo)
    if cur_state == RecordLessonStates.selecting_soloists.state:
        back_cb = "lesson_back:duration"
    else:
        back_cb = "lesson_back:attendance"
    await callback.message.edit_reply_markup(
        reply_markup=kb_multi_select(mine, selected, back_cb=back_cb),
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
            lesson = await lesson_service.create(
                teacher=teacher,
                lesson_type=LessonType.GROUP,
                lesson_date=lesson_date,
                duration_min=duration,
                attendees=attendees_csv,
            )
            extra = f"\nОтмечено: {len(attendee_ids)}" if attendee_ids else ""
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"✅ Групповое занятие записано!\nID: {lesson.lesson_id}\n"
                f"Дата: {format_date_display(lesson.date)}\n"
                f"Начислено: {lesson.earned} руб.{extra}\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type(),
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
            total_earned = sum(ls.earned for ls in lessons)
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"✅ Создано парных занятий: {len(lessons)}\n"
                f"Дата: {format_date_display(lesson_date)}\n"
                f"Пары: {'; '.join(pair_labels)}\n"
                f"Итого начислено: {total_earned} руб.\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type(),
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
            total_earned = sum(ls.earned for ls in lessons)
            names = ", ".join(n for _, n in students)
            await state.set_data({"lesson_date": lesson_date})
            await state.set_state(RecordLessonStates.choosing_kind)
            await callback.message.edit_text(
                f"✅ Создано соло-занятий: {len(lessons)}\n"
                f"Дата: {format_date_display(lesson_date)}\n"
                f"Ученики: {names}\n"
                f"Итого начислено: {total_earned} руб.\n\n"
                f"Продолжим? Выберите тип следующего занятия:",
                reply_markup=kb_lesson_type(),
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
