from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import TeacherRepository, LessonRepository
from bot.services import calc_earned, build_billing_rows
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import display_period, format_date_short_with_wd

logger = logging.getLogger(__name__)
router = Router(name="admin_profit")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _period_buttons() -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"profit_period:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="📅 За день...", callback_data="profit_day_picker")])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _calc_profit(
    period: str,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
) -> tuple[list[tuple[str, str, int, int, int]], int, int]:
    """Возвращает (строки по педагогам, итог_выручка, итог_зарплата).
    Строка: (teacher_id, имя, выручка, зарплата, кол-во занятий).
    """
    teachers = await teacher_repo.get_all()
    rows: list[tuple[str, str, int, int, int, int]] = []
    total_income = 0
    total_salary = 0
    for teacher in teachers:
        lessons = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)
        if not lessons:
            continue
        lesson_income = {
            ls.lesson_id: sum(b.amount for b in build_billing_rows(ls, teacher))
            for ls in lessons
        }
        income = sum(lesson_income.values())
        salary = sum(
            calc_earned(ls.type, ls.duration_min, teacher)
            for ls in lessons
            if lesson_income[ls.lesson_id] > 0
        )
        if income == 0 and salary == 0:
            continue
        billed = [ls for ls in lessons if lesson_income[ls.lesson_id] > 0]
        grp = sum(1 for ls in billed if ls.type == LessonType.GROUP)
        ind = len(billed) - grp
        rows.append((teacher.teacher_id, teacher.name, income, salary, grp, ind))
        total_income += income
        total_salary += salary
    return rows, total_income, total_salary


def _format_profit(title: str, rows: list[tuple[str, str, int, int, int, int]], total_income: int, total_salary: int) -> str:
    lines = [f"<b>📊 {title}</b>", ""]
    if not rows:
        lines.append("Занятий нет.")
        return "\n".join(lines)
    for _tid, name, income, salary, grp, ind in rows:
        profit = income - salary
        margin = round(profit / income * 100) if income else 0
        parts = []
        if grp:
            parts.append(f"👥 {grp}")
        if ind:
            parts.append(f"👤 {ind}")
        count_label = "  ".join(parts)
        lines.append(f"<b>{name}</b>  {count_label}")
        lines.append(f"  Выручка: {income} ₽  Зарплата: {salary} ₽")
        lines.append(f"  Прибыль: <b>{profit} ₽</b> ({margin}%)")
        lines.append("")
    lines += [
        "──────────────",
        f"Выручка:    {total_income} ₽",
        f"Зарплата: {total_salary} ₽",
        f"<b>Прибыль:  {total_income - total_salary} ₽</b>",
    ]
    return "\n".join(lines)


def _profit_keyboard(
    rows: list[tuple[str, str, int, int, int]],
    period: str,
    back_cb: str,
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"🔍 {name}",
            callback_data=f"profit_detail:{tid}:{period}",
        )]
        for tid, name, income, _s, _g, _i in rows if income > 0
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _lesson_dates_all(lesson_repo: LessonRepository, teacher_repo: TeacherRepository, year: int, month: int) -> set[date]:
    period = f"{year}-{month:02d}"
    teachers = await teacher_repo.get_all()
    result: set[date] = set()
    for teacher in teachers:
        lessons = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)
        for ls in lessons:
            try:
                result.add(date.fromisoformat(ls.date))
            except ValueError:
                pass
    return result


@router.callback_query(F.data == "profit:view")
async def cb_profit_view(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Выберите период:", reply_markup=_period_buttons())
    await callback.answer()


@router.callback_query(F.data.startswith("profit_period:"))
async def cb_profit_period(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    rows, total_income, total_salary = await _calc_profit(period, teacher_repo, lesson_repo)
    text = _format_profit(f"Прибыль за {display_period(period)}", rows, total_income, total_salary)
    await callback.message.edit_text(text, reply_markup=_profit_keyboard(rows, period, "profit:view"))
    await callback.answer()


@router.callback_query(F.data == "profit_day_picker")
async def cb_profit_day_picker(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    today = date.today()
    yesterday = today - timedelta(days=1)
    highlights = await _lesson_dates_all(lesson_repo, teacher_repo, today.year, today.month)
    await state.update_data(profit_cal_year=today.year, profit_cal_month=today.month)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Сегодня ({format_date_short_with_wd(today.isoformat())})",
            callback_data=f"profit_day_show:{today.isoformat()}",
        )],
        [InlineKeyboardButton(
            text=f"Вчера ({format_date_short_with_wd(yesterday.isoformat())})",
            callback_data=f"profit_day_show:{yesterday.isoformat()}",
        )],
        [InlineKeyboardButton(text="📅 Выбрать дату...", callback_data="profit_dday_open")],
        [InlineKeyboardButton(text="« Назад", callback_data="profit:view")],
    ])
    await callback.message.edit_text("Выберите дату:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "profit_dday_open")
async def cb_profit_dday_open(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    today = date.today()
    highlights = await _lesson_dates_all(lesson_repo, teacher_repo, today.year, today.month)
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(today.year, today.month, prefix="profit_dday",
                                 cancel_cb="profit_day_picker", highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_dday_nav:"))
async def cb_profit_dday_nav(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    highlights = await _lesson_dates_all(lesson_repo, teacher_repo, year, month)
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="profit_dday",
                                 cancel_cb="profit_day_picker", highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_dday_pick:"))
async def cb_profit_dday_pick(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    date_str = callback.data.split(":", 1)[1]
    await _show_day_profit(callback, date_str, teacher_repo, lesson_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("profit_day_show:"))
async def cb_profit_day_show(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    date_str = callback.data.split(":", 1)[1]
    await _show_day_profit(callback, date_str, teacher_repo, lesson_repo)
    await callback.answer()


async def _show_day_profit(
    callback: CallbackQuery,
    date_str: str,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
) -> None:
    rows, total_income, total_salary = await _calc_profit(date_str, teacher_repo, lesson_repo)
    title = f"Прибыль за {format_date_short_with_wd(date_str)}"
    text = _format_profit(title, rows, total_income, total_salary)
    await callback.message.edit_text(text, reply_markup=_profit_keyboard(rows, date_str, "profit_day_picker"))


@router.callback_query(F.data.startswith("profit_detail:"))
async def cb_profit_detail(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period = callback.data.split(":", 2)
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return

    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period)
    lessons = sorted(lessons, key=lambda ls: ls.date)

    is_day = len(period) == 10
    period_label = format_date_short_with_wd(period) if is_day else display_period(period)
    back_cb = f"profit_day_show:{period}" if is_day else f"profit_period:{period}"

    lines = [f"<b>{teacher.name} — {period_label}</b>", ""]
    total_income = total_salary = 0

    for ls in lessons:
        inc = sum(b.amount for b in build_billing_rows(ls, teacher))
        if inc == 0:
            continue
        sal = calc_earned(ls.type, ls.duration_min, teacher)
        profit = inc - sal
        date_prefix = f"{format_date_short_with_wd(ls.date)}  " if not is_day else ""
        kind = "👥" if ls.type.value == "group" else "👤"
        lines.append(f"{date_prefix}{kind} {ls.duration_min}мин")
        lines.append(f"  {inc} ₽ − {sal} ₽ = <b>{profit} ₽</b>")
        total_income += inc
        total_salary += sal

    if total_income == 0:
        lines.append("Нет тарифицируемых занятий.")
    else:
        total_profit = total_income - total_salary
        margin = round(total_profit / total_income * 100)
        lines += [
            "",
            "──────────────",
            f"Выручка: {total_income} ₽  Зарплата: {total_salary} ₽",
            f"<b>Прибыль: {total_profit} ₽ ({margin}%)</b>",
        ]

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=back_kb)
    await callback.answer()
