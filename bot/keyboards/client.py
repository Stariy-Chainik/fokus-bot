from __future__ import annotations
from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.dates import display_period


def kb_client_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Занятия", callback_data="client:lessons")],
        [InlineKeyboardButton(text="📋 Счета", callback_data="client:my_bills")],
        [InlineKeyboardButton(text="➕ Добавить ребёнка", callback_data="client:add_child")],
    ])


def kb_admin_approve_child(parent_tg_id: int, student_id: str) -> InlineKeyboardMarkup:
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
    from dateutil.relativedelta import relativedelta
    today = date.today()
    buttons = []
    for i in range(6):
        d = today - relativedelta(months=i)
        period = d.strftime("%Y-%m")
        buttons.append([InlineKeyboardButton(
            text=display_period(period), callback_data=f"cl_month:{student_id}:{period}",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"cl_stu:{student_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_lessons_back(student_id: str = "all") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« К занятиям", callback_data=f"cl_stu:{student_id}")],
        [InlineKeyboardButton(text="« Меню", callback_data="go:home")],
    ])


def kb_bills_list(periods: list[tuple[str, str, str]], student_id: str = "all") -> InlineKeyboardMarkup:
    """periods: list of (period_month, label, status_icon)"""
    buttons = [
        [InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"client_bill:{student_id}:{month}")]
        for month, label, icon in periods
    ]
    if student_id != "all":
        buttons.append([InlineKeyboardButton(text="« Назад", callback_data="client:my_bills")])
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_bill_detail(payment_ids: list[str], can_pay: bool, period_month: str, student_id: str = "all") -> InlineKeyboardMarkup:
    rows = []
    if can_pay:
        for pid in payment_ids:
            rows.append([InlineKeyboardButton(
                text="💳 Оплатить", callback_data=f"client_pay:{pid}",
            )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"cl_bills_stu:{student_id}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
