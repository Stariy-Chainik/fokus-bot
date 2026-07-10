from __future__ import annotations

from datetime import date

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.calendar import kb_calendar
from bot.keyboards.teacher import kb_lesson_list
from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.utils.constants import PAGE_SIZE
from bot.utils.dates import format_date_display

from ._base import (
    _date_filter_kb,
    _is_teacher,
    _locked_ids,
    _month_label,
    _month_picker_kb,
    _submitted_periods,
    router,
)


async def _get_mode(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("lm_mode", "view")


def _filter_lessons(
    lessons,
    *,
    filter_date: str | None,
    filter_month: str | None,
    filter_type: str | None,
):
    if filter_date:
        lessons = [lesson for lesson in lessons if lesson.date == filter_date]
    elif filter_month:
        lessons = [lesson for lesson in lessons if lesson.date[:7] == filter_month]

    if filter_type == "group":
        lessons = [lesson for lesson in lessons if lesson.type == LessonType.GROUP]
    elif filter_type == "individual":
        lessons = [lesson for lesson in lessons if lesson.type == LessonType.INDIVIDUAL]
    return lessons


async def _lessons_for_list(
    user: User,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    state: FSMContext,
    *,
    filter_date: str | None,
    filter_month: str | None,
    filter_type: str | None,
):
    mode = await _get_mode(state)
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    lessons = _filter_lessons(
        lessons,
        filter_date=filter_date,
        filter_month=filter_month,
        filter_type=filter_type,
    )
    periods = await _submitted_periods(user.teacher_id, submission_repo)
    if mode == "delete":
        lessons = [lesson for lesson in lessons if lesson.date[:7] not in periods]
    lessons.sort(key=lambda lesson: lesson.date, reverse=True)
    return lessons, periods


async def _show_lessons(
    callback: CallbackQuery,
    user: User,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    state: FSMContext,
    filter_date: str | None = None,
    filter_month: str | None = None,
    filter_type: str | None = None,
) -> None:
    mode = await _get_mode(state)
    lessons, periods = await _lessons_for_list(
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_date=filter_date,
        filter_month=filter_month,
        filter_type=filter_type,
    )

    if filter_date:
        filter_tag = filter_date
    elif filter_month:
        filter_tag = f"m-{filter_month}"
    else:
        filter_tag = "all"
    await state.update_data(lm_filter_tag=filter_tag, lm_filter_type=filter_type)

    if filter_date:
        title_fragment = f"за {format_date_display(filter_date)}"
    elif filter_month:
        title_fragment = f"за {_month_label(filter_month)}"
    else:
        title_fragment = ""
    type_fragment = {
        "group": " · групповые",
        "individual": " · индивидуальные",
    }.get(filter_type or "", "")

    locked = _locked_ids(lessons, periods)
    list_keyboard = kb_lesson_list(
        lessons,
        page=0,
        page_size=PAGE_SIZE,
        locked_ids=locked,
        filter_date=filter_date,
        filter_month=filter_month,
        filter_type=filter_type,
    )
    if not lessons:
        extra = " (или все в сданных периодах)" if mode == "delete" else ""
        await callback.message.edit_text(
            f"Занятий {title_fragment}{type_fragment} не найдено{extra}.",
            reply_markup=list_keyboard,
        )
        return

    if title_fragment:
        header = f"<b>Занятия {title_fragment}{type_fragment}</b> ({len(lessons)}):"
    else:
        header = f"<b>Занятия</b>{type_fragment} ({len(lessons)}):"
    await callback.message.edit_text(header, reply_markup=list_keyboard)


def _parse_type_code(code: str) -> str | None:
    return {"g": "group", "i": "individual"}.get(code)


def _parse_filter_tag(tag: str) -> tuple[str | None, str | None]:
    if tag.startswith("m-"):
        return None, tag[2:]
    if tag == "all":
        return None, None
    return tag, None


@router.callback_query(F.data == "teacher:my_lessons")
async def cb_my_lessons(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
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
async def cb_lesson_view(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
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
async def cb_lesson_delete(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
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
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
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
                today.year,
                today.month,
                prefix="lv",
                cancel_cb="teacher:my_lessons",
            ),
        )
        await callback.answer()
        return
    if value == "month":
        await callback.message.edit_text(
            "Выберите месяц:",
            reply_markup=_month_picker_kb(),
        )
        await callback.answer()
        return
    await _show_lessons(
        callback,
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_date=value,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("my_lessons_month:"))
async def cb_my_lessons_month(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    await _show_lessons(
        callback,
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_month=period,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lv_nav:"))
async def cb_lv_nav(callback: CallbackQuery, user: User | None) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    year, month = (int(value) for value in period.split("-"))
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(
            year,
            month,
            prefix="lv",
            cancel_cb="teacher:my_lessons",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lv_pick:"))
async def cb_lv_pick(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    filter_date = callback.data.split(":", 1)[1]
    await _show_lessons(
        callback,
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_date=filter_date,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lessons_page:"))
async def cb_lessons_page(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
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

    lessons, periods = await _lessons_for_list(
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_date=filter_date,
        filter_month=filter_month,
        filter_type=filter_type,
    )
    await callback.message.edit_reply_markup(
        reply_markup=kb_lesson_list(
            lessons,
            page=page,
            page_size=PAGE_SIZE,
            locked_ids=_locked_ids(lessons, periods),
            filter_date=filter_date,
            filter_month=filter_month,
            filter_type=filter_type,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lessons_type:"))
async def cb_lessons_type(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
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
        callback,
        user,
        lesson_repo,
        submission_repo,
        state,
        filter_date=filter_date,
        filter_month=filter_month,
        filter_type=filter_type,
    )
    await callback.answer()
