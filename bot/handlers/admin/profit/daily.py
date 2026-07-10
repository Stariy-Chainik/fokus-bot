from __future__ import annotations

from datetime import date, timedelta

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.calendar import kb_calendar
from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherRepository
from bot.services import ProfitService
from bot.utils.dates import display_period, format_date_short_with_wd

from ._base import (
    _format_profit,
    _is_admin,
    _lesson_dates_all,
    _profit_keyboard,
    router,
)


@router.callback_query(F.data == "profit_day_picker")
async def cb_profit_day_picker(
    callback: CallbackQuery,
    user: User | None,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    today = date.today()
    yesterday = today - timedelta(days=1)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Сегодня ({format_date_short_with_wd(today.isoformat())})",
            callback_data=f"profit_day_show:{today.isoformat()}",
        )],
        [InlineKeyboardButton(
            text=f"Вчера ({format_date_short_with_wd(yesterday.isoformat())})",
            callback_data=f"profit_day_show:{yesterday.isoformat()}",
        )],
        [InlineKeyboardButton(
            text="📅 Выбрать дату...",
            callback_data="profit_dday_open",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data="profit:view")],
    ])
    await callback.message.edit_text("Выберите дату:", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "profit_dday_open")
async def cb_profit_dday_open(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    today = date.today()
    highlights = await _lesson_dates_all(
        lesson_repo,
        teacher_repo,
        today.year,
        today.month,
    )
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(
            today.year,
            today.month,
            prefix="profit_dday",
            cancel_cb="profit_day_picker",
            highlight_dates=highlights,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_dday_nav:"))
async def cb_profit_dday_nav(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    year, month = (int(value) for value in period.split("-"))
    highlights = await _lesson_dates_all(
        lesson_repo,
        teacher_repo,
        year,
        month,
    )
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(
            year,
            month,
            prefix="profit_dday",
            cancel_cb="profit_day_picker",
            highlight_dates=highlights,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_dday_pick:"))
async def cb_profit_dday_pick(
    callback: CallbackQuery,
    user: User | None,
    profit_service: ProfitService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    date_string = callback.data.split(":", 1)[1]
    await _show_day_profit(callback, date_string, profit_service)
    await callback.answer()


@router.callback_query(F.data.startswith("profit_day_show:"))
async def cb_profit_day_show(
    callback: CallbackQuery,
    user: User | None,
    profit_service: ProfitService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    date_string = callback.data.split(":", 1)[1]
    await _show_day_profit(callback, date_string, profit_service)
    await callback.answer()


async def _show_day_profit(
    callback: CallbackQuery,
    date_string: str,
    profit_service: ProfitService,
) -> None:
    summary = await profit_service.get_lesson_summary(date_string)
    title = f"Прибыль за {format_date_short_with_wd(date_string)}"
    await callback.message.edit_text(
        _format_profit(title, summary),
        reply_markup=_profit_keyboard(
            summary.teacher_rows,
            date_string,
            "profit_day_picker",
        ),
    )


@router.callback_query(F.data.startswith("profit_detail:"))
async def cb_profit_detail(
    callback: CallbackQuery,
    user: User | None,
    profit_service: ProfitService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period = callback.data.split(":", 2)
    detail = await profit_service.get_teacher_detail(teacher_id, period)
    if detail is None:
        await callback.answer("Педагог не найден", show_alert=True)
        return

    is_day = len(period) == 10
    period_label = (
        format_date_short_with_wd(period) if is_day else display_period(period)
    )
    back_callback = (
        f"profit_day_show:{period}" if is_day else f"profit_period:{period}"
    )
    lines = [f"<b>{detail.teacher_name} — {period_label}</b>", ""]
    for row in detail.lessons:
        date_prefix = (
            f"{format_date_short_with_wd(row.date)}  " if not is_day else ""
        )
        kind = "👥" if row.lesson_type == LessonType.GROUP else "👤"
        lines.append(f"{date_prefix}{kind} {row.duration_min}мин")
        lines.append(
            f"  {row.income} ₽ − {row.salary} ₽ = <b>{row.profit} ₽</b>"
        )

    if detail.income == 0:
        lines.append("Нет тарифицируемых занятий.")
    else:
        lines += [
            "",
            "──────────────",
            f"Выручка: {detail.income} ₽  Зарплата: {detail.salary} ₽",
            f"<b>Прибыль: {detail.profit} ₽ ({detail.margin_percent}%)</b>",
        ]

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="« Назад", callback_data=back_callback),
        ]]),
    )
    await callback.answer()
