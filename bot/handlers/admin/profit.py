from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import TeacherRepository, LessonRepository, FinanceEntryRepository
from bot.services import calc_earned, build_billing_rows, PaymentService
from bot.states import FinanceEntryStates
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import display_period, format_date_short_with_wd, last_periods

logger = logging.getLogger(__name__)
router = Router(name="admin_profit")


from bot.handlers.access import is_admin as _is_admin


def _period_buttons() -> InlineKeyboardMarkup:
    periods = last_periods(6)
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


def _format_profit(
    title: str, rows: list[tuple[str, str, int, int, int, int]],
    total_income: int, total_salary: int,
    sub_rows: list[tuple[str, int, int]] | None = None,
    fin_entries: list | None = None,
) -> str:
    """sub_rows — абонементная выручка по группам (имя, учеников, сумма ₽).
    fin_entries — ручные записи FinanceEntry (доходы: турниры и т.п.;
    расходы: аренда и др.). Оба — только в месячном виде."""
    sub_total = sum(t for _, _, t in (sub_rows or []))
    incomes = [e for e in (fin_entries or []) if e.kind == "income"]
    expenses = [e for e in (fin_entries or []) if e.kind == "expense"]
    fin_income = sum(e.amount for e in incomes)
    fin_expense = sum(e.amount for e in expenses)

    lines = [f"<b>📊 {title}</b>", ""]
    if not rows and not sub_total and not fin_entries:
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
    if sub_rows:
        lines.append("💳 <b>Абонементы</b>")
        for gname, billed, total in sub_rows:
            lines.append(f"  {gname}: {total} ₽ ({billed} уч.)")
        lines.append(f"  <b>Итого абонементы: {sub_total} ₽</b>")
        lines.append("")
    if incomes:
        lines.append("🏆 <b>Прочие доходы</b>")
        for e in incomes:
            lines.append(f"  {e.title}: {e.amount} ₽")
        lines.append(f"  <b>Итого: {fin_income} ₽</b>")
        lines.append("")
    if expenses:
        lines.append("📉 <b>Расходы</b>")
        for e in expenses:
            lines.append(f"  {e.title}: {e.amount} ₽")
        lines.append(f"  <b>Итого: {fin_expense} ₽</b>")
        lines.append("")
    grand_income = total_income + sub_total + fin_income
    grand_expense = total_salary + fin_expense
    lines += [
        "──────────────",
        f"Выручка:    {grand_income} ₽",
        f"Зарплата: {total_salary} ₽",
    ]
    if fin_expense:
        lines.append(f"Расходы:   {fin_expense} ₽")
    lines.append(f"<b>Прибыль:  {grand_income - grand_expense} ₽</b>")
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


async def _period_view(
    period: str,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
    payment_service: PaymentService, finance_entry_repo: FinanceEntryRepository,
) -> tuple[str, InlineKeyboardMarkup]:
    """(text, kb) месячного вида «Прибыли»: занятия + абонементы + ручные записи."""
    rows, total_income, total_salary = await _calc_profit(period, teacher_repo, lesson_repo)
    # Абонементы и ручные записи — только в месячном виде.
    sub_rows = await payment_service.subscription_revenue_breakdown(period)
    fin_entries = await finance_entry_repo.get_by_period(period)
    text = _format_profit(
        f"Прибыль за {display_period(period)}", rows, total_income, total_salary,
        sub_rows=sub_rows, fin_entries=fin_entries,
    )
    kb = _profit_keyboard(rows, period, "profit:view")
    fin_buttons = [
        InlineKeyboardButton(text="➕ Доход", callback_data=f"fin:add:income:{period}"),
        InlineKeyboardButton(text="➕ Расход", callback_data=f"fin:add:expense:{period}"),
    ]
    if fin_entries:
        fin_buttons.append(InlineKeyboardButton(text="🗑", callback_data=f"fin:list:{period}"))
    kb.inline_keyboard.insert(len(kb.inline_keyboard) - 1, fin_buttons)
    return text, kb


@router.callback_query(F.data.startswith("profit_period:"))
async def cb_profit_period(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
    payment_service: PaymentService, finance_entry_repo: FinanceEntryRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    text, kb = await _period_view(period, teacher_repo, lesson_repo, payment_service, finance_entry_repo)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ─── Ручные доходы/расходы месяца (турниры, аренда и т.п.) ───────────────────

_FIN_KIND_LABELS = {"income": "доход", "expense": "расход"}


@router.callback_query(F.data.startswith("fin:add:"))
async def cb_fin_add(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, kind, period = callback.data.split(":", 3)
    await state.set_state(FinanceEntryStates.entering_title)
    await state.update_data(fin_kind=kind, fin_period=period)
    example = "Турнир «Осенний кубок»" if kind == "income" else "Аренда зала"
    await callback.message.edit_text(
        f"<b>➕ {_FIN_KIND_LABELS[kind].capitalize()} за {display_period(period)}</b>\n\n"
        f"Введите название (например, «{example}»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"profit_period:{period}")],
        ]),
    )
    await callback.answer()


@router.message(FinanceEntryStates.entering_title)
async def fin_title_entered(message: Message, state: FSMContext) -> None:
    title = " ".join((message.text or "").split())
    if not title:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    await state.update_data(fin_title=title[:100])
    await state.set_state(FinanceEntryStates.entering_amount)
    await message.answer(f"«{title[:100]}» — введите сумму в рублях (целое число):")


@router.message(FinanceEntryStates.entering_amount)
async def fin_amount_entered(
    message: Message, state: FSMContext,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
    payment_service: PaymentService, finance_entry_repo: FinanceEntryRepository,
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужно целое число рублей больше 0. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    kind, period, title = data["fin_kind"], data["fin_period"], data["fin_title"]
    entry = await finance_entry_repo.add(period, kind, title, int(raw))
    logger.info("Финансовая запись %s: %s «%s» %d ₽ за %s",
                entry.entry_id, kind, title, entry.amount, period)
    text, kb = await _period_view(period, teacher_repo, lesson_repo, payment_service, finance_entry_repo)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("fin:list:"))
async def cb_fin_list(
    callback: CallbackQuery, user: User | None,
    finance_entry_repo: FinanceEntryRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 2)[2]
    entries = await finance_entry_repo.get_by_period(period)
    if not entries:
        await callback.answer("Записей нет", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=f"✖ {'🏆' if e.kind == 'income' else '📉'} {e.title} — {e.amount} ₽",
            callback_data=f"fin:del:{e.entry_id}:{period}",
        )]
        for e in entries
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"profit_period:{period}")])
    await callback.message.edit_text(
        "<b>🗑 Удалить запись</b>\n\nНажмите на строку, чтобы удалить её:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fin:del:"))
async def cb_fin_delete(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, lesson_repo: LessonRepository,
    payment_service: PaymentService, finance_entry_repo: FinanceEntryRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, entry_id, period = callback.data.split(":", 3)
    ok = await finance_entry_repo.delete(entry_id)
    text, kb = await _period_view(period, teacher_repo, lesson_repo, payment_service, finance_entry_repo)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Удалено" if ok else "Уже удалено")


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
