from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    TeacherRepository, LessonRepository, TeacherPeriodSubmissionRepository,
)
from bot.services import calc_earned
from bot.keyboards.admin import kb_teacher_list, kb_back
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import display_period, format_date_short_with_wd
from bot.utils.lesson_stats import format_lesson_breakdown

logger = logging.getLogger(__name__)
router = Router(name="admin_salaries")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _period_buttons(teacher_id: str, back_cb: str) -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"salary_period:{teacher_id}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "salaries:view")
async def cb_salaries_choose_teacher(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:menu"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите педагога:</b>",
        reply_markup=kb_teacher_list(teachers, "salary_teacher", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_teacher:"))
async def cb_salary_choose_period(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    """Вход из salaries:view — back ведёт в список педагогов."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(salary_back_cb="salaries:view")
    await callback.message.edit_text(
        f"<b>Выберите период для педагога {teacher_id}:</b>",
        reply_markup=_period_buttons(teacher_id, back_cb="salaries:view"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tc_salary:"))
async def cb_salary_from_card(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    """Вход из карточки педагога — back ведёт обратно в карточку."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    back_cb = f"teacher_card:{teacher_id}"
    await state.update_data(salary_back_cb=back_cb)
    await callback.message.edit_text(
        f"<b>Выберите период для педагога {teacher_id}:</b>",
        reply_markup=_period_buttons(teacher_id, back_cb=back_cb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_period:"))
async def cb_salary_show(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period_month = callback.data.split(":", 2)
    teacher = await teacher_repo.get_by_id(teacher_id)
    data = await state.get_data()
    # «Назад» из счёта периода ведёт обратно к выбору периода. Кэшированный
    # entry-point определяет, куда ведёт «Назад» из выбора периода.
    period_back = data.get("salary_back_cb", f"teacher_card:{teacher_id}")
    back_cb = (
        f"tc_salary:{teacher_id}"
        if period_back == f"teacher_card:{teacher_id}"
        else f"salary_teacher:{teacher_id}"
    )
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return

    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    total_earned = sum(calc_earned(ls.type, ls.duration_min, teacher) for ls in lessons)
    group, ind, gline, iline = format_lesson_breakdown(lessons)
    total = group + ind

    submission = await submission_repo.get_by_teacher_and_period(teacher_id, period_month)
    period_status = "✅ Сдан педагогом" if submission else "⏳ Открыт"

    lines = [
        f"<b>Педагог: {teacher.name}</b>",
        f"Период: {display_period(period_month)}",
        "",
        f"Всего занятий: {total}",
        f"👥 Групповые ({group}): {gline}",
        f"👤 Индивидуальные ({ind}): {iline}",
        f"Начислено: {total_earned} руб.",
        "",
        f"Период: {period_status}",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=kb_back(back_cb))
    await callback.answer()


# ── Выплата за день ────────────────────────────────────────────────────────────

async def _show_day_salary(
    callback: CallbackQuery,
    teacher_id: str,
    date_str: str,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
) -> None:
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, date_str)
    total_earned = sum(calc_earned(ls.type, ls.duration_min, teacher) for ls in lessons)
    group, ind, gline, iline = format_lesson_breakdown(lessons)
    total = group + ind

    lines = [
        f"<b>Педагог: {teacher.name}</b>",
        f"Дата: {format_date_short_with_wd(date_str)}",
        "",
    ]
    if total == 0:
        lines.append("Занятий нет.")
    else:
        lines.append(f"Занятий: {total}")
        if group:
            lines.append(f"  👥 Групповые ({group}): {gline}")
        if ind:
            lines.append(f"  👤 Индивидуальные ({ind}): {iline}")
        lines.append(f"\n<b>К выплате: {total_earned} руб.</b>")

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data=f"salary_day:{teacher_id}")],
        [InlineKeyboardButton(text="🏠 Карточка педагога", callback_data=f"teacher_card:{teacher_id}")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=back_kb)


@router.callback_query(F.data.startswith("salary_day:"))
async def cb_salary_day(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(salary_day_teacher_id=teacher_id)
    today = date.today()
    yesterday = today - timedelta(days=1)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Сегодня ({format_date_short_with_wd(today.isoformat())})",
            callback_data=f"salary_day_show:{teacher_id}:{today.isoformat()}",
        )],
        [InlineKeyboardButton(
            text=f"Вчера ({format_date_short_with_wd(yesterday.isoformat())})",
            callback_data=f"salary_day_show:{teacher_id}:{yesterday.isoformat()}",
        )],
        [InlineKeyboardButton(text="📅 Выбрать дату...", callback_data=f"salary_dday_cal:{teacher_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"teacher_card:{teacher_id}")],
    ])
    await callback.message.edit_text("Выберите дату:", reply_markup=kb)
    await callback.answer()


async def _lesson_dates(lesson_repo: LessonRepository, teacher_id: str, year: int, month: int) -> set[date]:
    period = f"{year}-{month:02d}"
    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period)
    result: set[date] = set()
    for ls in lessons:
        try:
            result.add(date.fromisoformat(ls.date))
        except ValueError:
            pass
    return result


@router.callback_query(F.data.startswith("salary_dday_cal:"))
async def cb_salary_dday_cal(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(salary_day_teacher_id=teacher_id)
    today = date.today()
    highlights = await _lesson_dates(lesson_repo, teacher_id, today.year, today.month)
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(today.year, today.month, prefix="salary_dday",
                                 cancel_cb=f"salary_day:{teacher_id}",
                                 highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_dday_nav:"))
async def cb_salary_dday_nav(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    data = await state.get_data()
    teacher_id = data.get("salary_day_teacher_id", "")
    highlights = await _lesson_dates(lesson_repo, teacher_id, year, month)
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="salary_dday",
                                 cancel_cb=f"salary_day:{teacher_id}",
                                 highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_dday_pick:"))
async def cb_salary_dday_pick(
    callback: CallbackQuery,
    user: User | None,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
    state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    date_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    teacher_id = data.get("salary_day_teacher_id", "")
    await _show_day_salary(callback, teacher_id, date_str, teacher_repo, lesson_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("salary_day_show:"))
async def cb_salary_day_show(
    callback: CallbackQuery,
    user: User | None,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, date_str = callback.data.split(":", 2)
    await _show_day_salary(callback, teacher_id, date_str, teacher_repo, lesson_repo)
    await callback.answer()
