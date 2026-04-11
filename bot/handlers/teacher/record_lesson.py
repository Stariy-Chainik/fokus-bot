from __future__ import annotations
"""
Педагог: FSM «Отметить занятие».
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
from bot.keyboards.teacher import kb_lesson_type, kb_duration, kb_yes_no, kb_student_search_results, PAGE_SIZE, kb_teacher_menu
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="teacher_record_lesson")

_confirming_lesson_ids: set[str] = set()


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


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


# ─── Дата ─────────────────────────────────────────────────────────────────────

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
    await state.set_state(RecordLessonStates.choosing_type)
    await callback.message.edit_text(
        f"Дата: {format_date_display(value)}\n\nТип занятия:", reply_markup=kb_lesson_type()
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
    await state.set_state(RecordLessonStates.choosing_type)
    await message.answer(
        f"Дата: {format_date_display(lesson_date)}\n\nТип занятия:", reply_markup=kb_lesson_type()
    )


# ─── Тип занятия ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_type:"), RecordLessonStates.choosing_type)
async def cb_lesson_type_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    lesson_type = callback.data.split(":", 1)[1]
    await state.update_data(lesson_type=lesson_type)
    if lesson_type == "group":
        await state.set_state(RecordLessonStates.choosing_duration)
        await callback.message.edit_text("Групповое занятие.\n\nВыберите длительность:", reply_markup=kb_duration())
    else:
        await state.set_state(RecordLessonStates.searching_student_1)
        await callback.message.edit_text(
            "Введите первые буквы имени ученика\n(или пробел для просмотра всего списка):"
        )
    await callback.answer()


# ─── Поиск ученика 1 ──────────────────────────────────────────────────────────

async def _linked_students(teacher_id: str, prefix: str, ts_repo, student_repo):
    student_ids = await ts_repo.get_students_for_teacher(teacher_id)
    all_students = await student_repo.get_all()
    linked = [s for s in all_students if s.student_id in student_ids]
    if prefix.strip():
        linked = [s for s in linked if s.name.lower().startswith(prefix.lower())]
    return linked


@router.message(RecordLessonStates.searching_student_1)
async def msg_search_student_1(
    message: Message, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    prefix = (message.text or "").strip()
    students = await _linked_students(user.teacher_id, prefix, ts_repo, student_repo)
    if not students:
        await message.answer("Ученики не найдены. Попробуйте другой запрос:")
        return
    await state.update_data(search_results_1=[s.student_id for s in students])
    await state.set_state(RecordLessonStates.choosing_student_1)
    await message.answer(
        f"Найдено {len(students)}. Выберите:",
        reply_markup=kb_student_search_results(students[:PAGE_SIZE], "pick_student_1", page=0, total=len(students)),
    )


@router.callback_query(F.data.startswith("page:pick_student_1:"))
async def cb_page_student_1(callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository) -> None:
    page = int(callback.data.split(":")[-1])
    data = await state.get_data()
    ids = data.get("search_results_1", [])
    students = [s for s in await student_repo.get_all() if s.student_id in ids]
    start = page * PAGE_SIZE
    await callback.message.edit_reply_markup(
        reply_markup=kb_student_search_results(students[start:start + PAGE_SIZE], "pick_student_1", page=page, total=len(students))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pick_student_1:"), RecordLessonStates.choosing_student_1)
async def cb_pick_student_1(callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository) -> None:
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await state.update_data(student_1_id=student_id, student_1_name=student.name)
    await state.set_state(RecordLessonStates.asking_second_student)
    await callback.message.edit_text(
        f"Ученик 1: {student.name}\n\nДобавить второго ученика?",
        reply_markup=kb_yes_no("second_student:yes", "second_student:no"),
    )
    await callback.answer()


# ─── Второй ученик ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "second_student:no", RecordLessonStates.asking_second_student)
async def cb_no_second_student(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(student_2_id=None, student_2_name=None)
    await state.set_state(RecordLessonStates.choosing_duration)
    await callback.message.edit_text("Выберите длительность:", reply_markup=kb_duration())
    await callback.answer()


@router.callback_query(F.data == "second_student:yes", RecordLessonStates.asking_second_student)
async def cb_yes_second_student(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RecordLessonStates.searching_student_2)
    await callback.message.edit_text("Введите первые буквы имени второго ученика:")
    await callback.answer()


@router.message(RecordLessonStates.searching_student_2)
async def msg_search_student_2(
    message: Message, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    prefix = (message.text or "").strip()
    data = await state.get_data()
    students = await _linked_students(user.teacher_id, prefix, ts_repo, student_repo)
    students = [s for s in students if s.student_id != data.get("student_1_id")]
    if not students:
        await message.answer("Ученики не найдены. Попробуйте другой запрос:")
        return
    await state.update_data(search_results_2=[s.student_id for s in students])
    await state.set_state(RecordLessonStates.choosing_student_2)
    await message.answer(
        f"Найдено {len(students)}. Выберите второго:",
        reply_markup=kb_student_search_results(students[:PAGE_SIZE], "pick_student_2", page=0, total=len(students)),
    )


@router.callback_query(F.data.startswith("page:pick_student_2:"))
async def cb_page_student_2(callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository) -> None:
    page = int(callback.data.split(":")[-1])
    data = await state.get_data()
    ids = data.get("search_results_2", [])
    students = [s for s in await student_repo.get_all() if s.student_id in ids]
    start = page * PAGE_SIZE
    await callback.message.edit_reply_markup(
        reply_markup=kb_student_search_results(students[start:start + PAGE_SIZE], "pick_student_2", page=page, total=len(students))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pick_student_2:"), RecordLessonStates.choosing_student_2)
async def cb_pick_student_2(callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository) -> None:
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await state.update_data(student_2_id=student_id, student_2_name=student.name)
    await state.set_state(RecordLessonStates.choosing_duration)
    data = await state.get_data()
    await callback.message.edit_text(
        f"Ученик 1: {data.get('student_1_name')}\nУченик 2: {student.name}\n\nВыберите длительность:",
        reply_markup=kb_duration(),
    )
    await callback.answer()


# ─── Длительность + подтверждение ────────────────────────────────────────────

@router.callback_query(F.data.startswith("duration:"), RecordLessonStates.choosing_duration)
async def cb_duration(callback: CallbackQuery, state: FSMContext) -> None:
    duration = int(callback.data.split(":", 1)[1])
    await state.update_data(duration_min=duration)
    data = await state.get_data()

    lesson_type = data.get("lesson_type", "")
    lesson_date = data.get("lesson_date", "")
    lines = [
        "Подтвердите занятие:", "",
        f"Дата: {format_date_display(lesson_date)}",
        f"Тип: {'Групповое' if lesson_type == 'group' else 'Индивидуальное'}",
    ]
    if data.get("student_1_name"):
        lines.append(f"Ученик 1: {data['student_1_name']}")
    if data.get("student_2_name"):
        lines.append(f"Ученик 2: {data['student_2_name']}")
    lines.append(f"Длительность: {duration} мин")

    await state.set_state(RecordLessonStates.confirming)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_lesson")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="teacher:cancel_lesson")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_lesson", RecordLessonStates.confirming)
async def cb_confirm_lesson(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
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
        await state.clear()
        teacher = await teacher_repo.get_by_id(user.teacher_id)
        if not teacher:
            await callback.message.edit_text("Педагог не найден. Обратитесь к администратору.")
            return

        lesson = await lesson_service.create(
            teacher=teacher,
            lesson_type=LessonType(data["lesson_type"]),
            lesson_date=data["lesson_date"],
            duration_min=data["duration_min"],
            student_1_id=data.get("student_1_id"),
            student_1_name=data.get("student_1_name"),
            student_2_id=data.get("student_2_id"),
            student_2_name=data.get("student_2_name"),
        )
        await callback.message.edit_text(
            f"Занятие записано!\nID: {lesson.lesson_id}\n"
            f"Дата: {format_date_display(lesson.date)}\n"
            f"Начислено: {lesson.earned} руб.",
            reply_markup=kb_teacher_menu(),
        )
    except ValueError as exc:
        await callback.message.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        logger.error("Ошибка записи занятия: %s", exc)
        await callback.message.edit_text("Ошибка при сохранении занятия. Попробуйте позже.")
    finally:
        _confirming_lesson_ids.discard(lock_key)

    await callback.answer()
