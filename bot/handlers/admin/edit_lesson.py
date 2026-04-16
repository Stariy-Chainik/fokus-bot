from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import LessonRepository, TeacherRepository
from bot.services import LessonService
from bot.keyboards.admin import kb_teacher_list, kb_back
from bot.keyboards.teacher import kb_lesson_list
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="admin_edit_lesson")
PAGE_SIZE = 20

_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _month_label(ym: str) -> str:
    y, m = ym.split("-")
    return f"{_MONTHS_RU[int(m) - 1]} {y}"


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _date_filter_kb(teacher_id: str) -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"aedl_pick:{teacher_id}:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"aedl_pick:{teacher_id}:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data=f"aedl_manual:{teacher_id}")],
        [InlineKeyboardButton(text="📋 За месяц", callback_data=f"aedl_month_pick:{teacher_id}")],
        [InlineKeyboardButton(text="📚 Все занятия", callback_data=f"aedl_all:{teacher_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:edit_lesson")],
    ])


def _month_picker_kb(teacher_id: str) -> InlineKeyboardMarkup:
    today = date.today()
    cur_y, cur_m = today.year, today.month
    prev_y, prev_m = _shift_month(cur_y, cur_m, -1)
    cur_ym = f"{cur_y:04d}-{cur_m:02d}"
    prev_ym = f"{prev_y:04d}-{prev_m:02d}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_month_label(cur_ym), callback_data=f"aedl_month:{teacher_id}:{cur_ym}")],
        [InlineKeyboardButton(text=_month_label(prev_ym), callback_data=f"aedl_month:{teacher_id}:{prev_ym}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"aedl_dates:{teacher_id}")],
    ])


async def _show_lessons(
    callback: CallbackQuery, lesson_repo: LessonRepository, teacher_id: str,
    filter_date: str | None = None, filter_month: str | None = None,
) -> None:
    lessons = await lesson_repo.get_by_teacher(teacher_id)
    if filter_date:
        lessons = [ls for ls in lessons if ls.date == filter_date]
    elif filter_month:
        lessons = [ls for ls in lessons if ls.date[:7] == filter_month]
    lessons.sort(key=lambda ls: ls.date, reverse=True)

    if filter_date:
        title_frag = f"за {format_date_display(filter_date)}"
    elif filter_month:
        title_frag = f"за {_month_label(filter_month)}"
    else:
        title_frag = ""

    back_cb = f"aedl_dates:{teacher_id}"
    if not lessons:
        await callback.message.edit_text(
            f"Занятий {title_frag} не найдено.",
            reply_markup=kb_back(back_cb),
        )
        return
    header = f"<b>Занятия {title_frag}</b> ({len(lessons)}):" if title_frag else f"<b>Занятия</b> ({len(lessons)}):"
    await callback.message.edit_text(
        header,
        reply_markup=kb_lesson_list(
            lessons, page=0, page_size=PAGE_SIZE,
            filter_date=filter_date, filter_month=filter_month,
            back_cb=back_cb,
        ),
    )


@router.callback_query(F.data == "admin:edit_lesson")
async def cb_edit_lesson_choose_teacher(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
    state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:menu"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите педагога:</b>",
        reply_markup=kb_teacher_list(teachers, "aedl_t", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_t:"))
async def cb_admin_lessons_dates(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(aedl_teacher_id=teacher_id)
    await callback.message.edit_text(
        "<b>За какую дату показать занятия?</b>",
        reply_markup=_date_filter_kb(teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_dates:"))
async def cb_admin_lessons_dates_back(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(aedl_teacher_id=teacher_id)
    await callback.message.edit_text(
        "<b>За какую дату показать занятия?</b>",
        reply_markup=_date_filter_kb(teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_pick:"))
async def cb_admin_lessons_pick(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, day = callback.data.split(":", 2)
    await _show_lessons(callback, lesson_repo, teacher_id, filter_date=day)
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_all:"))
async def cb_admin_lessons_all(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await _show_lessons(callback, lesson_repo, teacher_id)
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_month_pick:"))
async def cb_admin_lessons_month_pick(
    callback: CallbackQuery, user: User | None,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await callback.message.edit_text(
        "Выберите месяц:", reply_markup=_month_picker_kb(teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_month:"))
async def cb_admin_lessons_month(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, ym = callback.data.split(":", 2)
    await _show_lessons(callback, lesson_repo, teacher_id, filter_month=ym)
    await callback.answer()


@router.callback_query(F.data.startswith("aedl_manual:"))
async def cb_admin_lessons_calendar(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(aedl_teacher_id=teacher_id)
    today = date.today()
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(
            today.year, today.month, prefix="aedlc",
            cancel_cb=f"aedl_dates:{teacher_id}",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedlc_nav:"))
async def cb_admin_lessons_cal_nav(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    data = await state.get_data()
    teacher_id = data.get("aedl_teacher_id", "")
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(
            year, month, prefix="aedlc",
            cancel_cb=f"aedl_dates:{teacher_id}",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aedlc_pick:"))
async def cb_admin_lessons_cal_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    day = callback.data.split(":", 1)[1]
    data = await state.get_data()
    teacher_id = data.get("aedl_teacher_id")
    if not teacher_id:
        await callback.answer("Сессия истекла, начните сначала", show_alert=True)
        return
    await _show_lessons(callback, lesson_repo, teacher_id, filter_date=day)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_lesson:"))
async def cb_admin_delete_lesson(
    callback: CallbackQuery, user: User | None, lesson_service: LessonService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    ok = await lesson_service.delete(lesson_id)
    text = f"Занятие {lesson_id} удалено." if ok else "Занятие не найдено."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:edit_lesson"))
    await callback.answer()
