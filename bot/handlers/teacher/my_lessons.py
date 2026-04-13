from __future__ import annotations
import logging
from datetime import date, timedelta, datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.services import LessonService
from bot.states import MyLessonsStates
from bot.keyboards.teacher import kb_lesson_list, kb_lesson_detail, kb_teacher_menu
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_lessons")
PAGE_SIZE = 20


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


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
        [InlineKeyboardButton(text="📋 Все занятия", callback_data="my_lessons_date:all")],
        [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
    ])


async def _submitted_periods(teacher_id: str, submission_repo: TeacherPeriodSubmissionRepository) -> set[str]:
    subs = await submission_repo.get_by_teacher(teacher_id)
    return {s.period_month for s in subs}


def _locked_ids(lessons, submitted_periods: set[str]) -> set[str]:
    return {ls.lesson_id for ls in lessons if ls.date[:7] in submitted_periods}


async def _show_lessons(
    callback: CallbackQuery, user: User, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    filter_date: str | None, state: FSMContext,
) -> None:
    await state.clear()
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    if filter_date:
        lessons = [ls for ls in lessons if ls.date == filter_date]
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    if not lessons:
        title = f"за {format_date_display(filter_date)}" if filter_date else ""
        await callback.message.edit_text(
            f"Занятий {title} не найдено.", reply_markup=_date_filter_kb(),
        )
        return
    periods = await _submitted_periods(user.teacher_id, submission_repo)
    locked = _locked_ids(lessons, periods)
    header = (
        f"<b>Занятия за {format_date_display(filter_date)}</b> ({len(lessons)}):"
        if filter_date else f"<b>Все занятия</b> ({len(lessons)}):"
    )
    await callback.message.edit_text(
        header,
        reply_markup=kb_lesson_list(
            lessons, page=0, page_size=PAGE_SIZE,
            locked_ids=locked, filter_date=filter_date,
        ),
    )


@router.callback_query(F.data == "teacher:my_lessons")
async def cb_my_lessons(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "<b>Мои занятия</b>\n"
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
        await state.set_state(MyLessonsStates.entering_custom_date)
        await callback.message.edit_text(
            "Введите дату в формате ДД.ММ.ГГГГ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_lessons")],
            ]),
        )
        await callback.answer()
        return
    filter_date = None if value == "all" else value
    await _show_lessons(callback, user, lesson_repo, submission_repo, filter_date, state)
    await callback.answer()


@router.message(MyLessonsStates.entering_custom_date)
async def msg_custom_date(
    message: Message, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        return
    text = (message.text or "").strip()
    filter_date = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            filter_date = datetime.strptime(text, fmt).date().isoformat()
            break
        except ValueError:
            pass
    if filter_date is None:
        await message.answer("Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:")
        return
    await state.clear()
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    lessons = [ls for ls in lessons if ls.date == filter_date]
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    if not lessons:
        await message.answer(
            f"Занятий за {format_date_display(filter_date)} не найдено.",
            reply_markup=_date_filter_kb(),
        )
        return
    periods = await _submitted_periods(user.teacher_id, submission_repo)
    locked = _locked_ids(lessons, periods)
    await message.answer(
        f"<b>Занятия за {format_date_display(filter_date)}</b> ({len(lessons)}):",
        reply_markup=kb_lesson_list(
            lessons, page=0, page_size=PAGE_SIZE,
            locked_ids=locked, filter_date=filter_date,
        ),
    )


@router.callback_query(F.data.startswith("lessons_page:"))
async def cb_lessons_page(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[1])
    filter_tag = parts[2] if len(parts) > 2 else "all"
    filter_date = None if filter_tag == "all" else filter_tag

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    if filter_date:
        lessons = [ls for ls in lessons if ls.date == filter_date]
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    periods = await _submitted_periods(user.teacher_id, submission_repo)
    locked = _locked_ids(lessons, periods)
    await callback.message.edit_reply_markup(
        reply_markup=kb_lesson_list(
            lessons, page=page, page_size=PAGE_SIZE,
            locked_ids=locked, filter_date=filter_date,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_detail:"))
async def cb_lesson_detail(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or lesson.teacher_id != user.teacher_id:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    periods = await _submitted_periods(user.teacher_id, submission_repo)
    locked = lesson.date[:7] in periods

    lines = [
        f"<b>Занятие {lesson.lesson_id}</b>",
        f"Дата: {format_date_display(lesson.date)}",
        f"Тип: {'Групповое' if lesson.type == LessonType.GROUP else 'Индивидуальное'}",
        f"Длительность: {lesson.duration_min} мин",
    ]
    if lesson.student_1_name:
        lines.append(f"Ученик 1: {lesson.student_1_name}")
    if lesson.student_2_name:
        lines.append(f"Ученик 2: {lesson.student_2_name}")
    if locked:
        lines.append("")
        lines.append("🔒 Период сдан — редактирование недоступно.")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb_lesson_detail(lesson, locked))
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("delete_lesson:"))
async def cb_delete_lesson_confirm(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or lesson.teacher_id != user.teacher_id:
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    periods = await _submitted_periods(user.teacher_id, submission_repo)
    if lesson.date[:7] in periods:
        await callback.answer(
            "🔒 Период сдан — обратитесь к администратору.", show_alert=True,
        )
        return
    await callback.message.edit_text(
        f"Удалить занятие {lesson_id} от {format_date_display(lesson.date)}?\n"
        "Связанные billing-записи тоже будут удалены.",
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
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if lesson and lesson.teacher_id != user.teacher_id:
        await callback.answer("Нет доступа к этому занятию", show_alert=True)
        return
    try:
        ok = await lesson_service.delete(lesson_id)
    except PermissionError:
        await callback.answer(
            "🔒 Период сдан — обратитесь к администратору.", show_alert=True,
        )
        return
    text = f"Занятие {lesson_id} удалено." if ok else "Занятие не найдено."
    await callback.message.edit_text(text, reply_markup=kb_teacher_menu())
    await callback.answer()
