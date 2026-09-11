from __future__ import annotations
from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.dates import display_period, last_periods
from bot.screens.adapters import to_aiogram_markup
from bot.screens.parent_menu import menu_rows, welcome_text
from bot.screens.parent_bills import bill_back_rows


def client_welcome_text(students: list) -> str:
    """Шапка главного экрана родителя: за каких детей открыт кабинет."""
    return welcome_text(students)


def kb_client_menu(can_switch_athlete: bool = False) -> InlineKeyboardMarkup:
    return to_aiogram_markup(menu_rows(can_switch_athlete))


def kb_admin_approve_child(parent_tg_id, student_id: str) -> InlineKeyboardMarkup:
    """parent_tg_id — tg_id или строка адреса (`m<id>` для MAX, см. parent_notifier.fmt_addr)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"admin_child_ok:{parent_tg_id}:{student_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_child_no:{parent_tg_id}:{student_id}"),
    ]])


def kb_client_student_select(students: list, section: str) -> InlineKeyboardMarkup:
    """Выбор ученика. section = 'lessons' | 'bills'"""
    prefix = "cl_stu" if section == "lessons" else "cl_bills_stu"
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"{prefix}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="👨‍👩‍👧 Все вместе", callback_data=f"{prefix}:all")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_lessons_period_select(student_id: str = "all") -> InlineKeyboardMarkup:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня", callback_data=f"cl_date:{student_id}:{today.isoformat()}"),
            InlineKeyboardButton(text="Завтра", callback_data=f"cl_date:{student_id}:{tomorrow.isoformat()}"),
        ],
        [
            InlineKeyboardButton(text="📅 Выбрать дату", callback_data=f"cl_calendar_s:{student_id}"),
            InlineKeyboardButton(text="📆 За месяц", callback_data=f"cl_month_list_s:{student_id}"),
        ],
        [InlineKeyboardButton(text="« Меню", callback_data="go:home")],
    ])


def kb_lessons_month_list(student_id: str = "all") -> InlineKeyboardMarkup:
    buttons = []
    for period in last_periods(6):
        buttons.append([InlineKeyboardButton(
            text=display_period(period), callback_data=f"cl_month:{student_id}:{period}",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"cl_stu:{student_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_lessons_month_filter(
    student_id: str, period: str,
    teachers: list, active: str = "all",
    pay_amount: int = 0,
) -> InlineKeyboardMarkup:
    """Экран занятий за месяц: фильтр по педагогу (teachers: [(id, имя)]).
    pay_amount > 0 — кнопка «Оплатить N ₽» (ведёт в счёт; при фильтре по педагогу — только он)."""
    rows = []
    if pay_amount > 0 and student_id != "all":
        target = f"client_pay:{student_id}:{period}" + (f":{active}" if active != "all" else "")
        rows.append([InlineKeyboardButton(text=f"💳 Оплатить {pay_amount} ₽", callback_data=target)])
    if len(teachers) > 1:
        mark = " ✓" if active == "all" else ""
        rows.append([InlineKeyboardButton(
            text=f"👥 Все педагоги{mark}",
            callback_data=f"cl_month_t:{student_id}:{period}:all",
        )])
        for tid, name in teachers:
            mark = " ✓" if tid == active else ""
            rows.append([InlineKeyboardButton(
                text=f"👨‍🏫 {name}{mark}",
                callback_data=f"cl_month_t:{student_id}:{period}:{tid}",
            )])
    rows.append([InlineKeyboardButton(text="« К занятиям", callback_data=f"cl_stu:{student_id}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_lessons_back(student_id: str = "all") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« К занятиям", callback_data=f"cl_stu:{student_id}")],
        [InlineKeyboardButton(text="« Меню", callback_data="go:home")],
    ])


def kb_bills_list(
    periods: list[tuple[str, str, str]], student_id: str = "all",
    show_back: bool = True,
    more_cb: str | None = None,
    recent_cb: str | None = None,
) -> InlineKeyboardMarkup:
    """periods: list of (period_month, label, status_icon).
    show_back=False — у родителя один ребёнок: экрана выбора ученика нет,
    «Назад» вёл бы на этот же экран (кнопка выглядела бы неработающей).
    more_cb — кнопка «📆 Другие месяцы»; recent_cb — «« К текущим месяцам»."""
    buttons = [
        [InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"client_bill:{student_id}:{month}")]
        for month, label, icon in periods
    ]
    if more_cb:
        buttons.append([InlineKeyboardButton(text="📆 Другие месяцы", callback_data=more_cb)])
    if recent_cb:
        buttons.append([InlineKeyboardButton(text="« К текущим месяцам", callback_data=recent_cb)])
    if student_id != "all" and show_back:
        buttons.append([InlineKeyboardButton(text="« Назад", callback_data="client:my_bills")])
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_bill_detail(payment_ids: list[str], can_pay: bool, period_month: str, student_id: str = "all") -> InlineKeyboardMarkup:
    rows = []
    if can_pay:
        rows.append([InlineKeyboardButton(
            text="💳 Оплатить",
            callback_data=f"client_pay:{student_id}:{period_month}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"cl_bills_stu:{student_id}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_bill_back(student_id: str, period_month: str) -> InlineKeyboardMarkup:
    return to_aiogram_markup(bill_back_rows(student_id, period_month))


def kb_payment_method(
    student_id: str, period_month: str,
    cash: bool, bank: bool, sbp: bool, yookassa: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    if yookassa:
        rows.append([InlineKeyboardButton(text="📱 СБП онлайн", callback_data=f"pay_method:ysbp:{student_id}:{period_month}")])
    if cash:
        rows.append([InlineKeyboardButton(text="💵 Наличные", callback_data=f"pay_method:cash:{student_id}:{period_month}")])
    if bank:
        rows.append([InlineKeyboardButton(text="🏦 По реквизитам", callback_data=f"pay_method:bank:{student_id}:{period_month}")])
    if sbp:
        rows.append([InlineKeyboardButton(text="📱 СБП", callback_data=f"pay_method:sbp:{student_id}:{period_month}")])
    rows.append([InlineKeyboardButton(text="« К счёту", callback_data=f"client_bill:{student_id}:{period_month}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_pay_cash(student_id: str, period_month: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Уведомить об оплате", callback_data=f"cash_notify:{student_id}:{period_month}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"client_pay:{student_id}:{period_month}")],
    ])


def kb_pay_receipt(method: str, student_id: str, period_month: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Прикрепить чек", callback_data=f"receipt_upload:{method}:{student_id}:{period_month}")],
        [InlineKeyboardButton(text="« Назад", callback_data=f"client_pay:{student_id}:{period_month}")],
    ])


def kb_cancel_receipt(student_id: str, period_month: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Отмена", callback_data=f"client_pay:{student_id}:{period_month}")],
    ])
