from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_client_menu(
    is_admin: bool = False, is_teacher: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="client:schedule")],
        [InlineKeyboardButton(text="💳 Мои счета", callback_data="client:billing")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🔄 Режим администратора", callback_data="mode:admin")])
    if is_teacher:
        rows.append([InlineKeyboardButton(text="🔄 Режим педагога", callback_data="mode:teacher")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_client_periods(periods: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """periods: list[(period_month YYYY-MM, display label)]."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"client_period:{pm}")]
        for pm, label in periods
    ]
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="client:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_client_schedule_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« К периодам", callback_data="client:schedule")],
        [InlineKeyboardButton(text="« В меню", callback_data="client:menu")],
    ])


def kb_client_invoices(invoices: list) -> InlineKeyboardMarkup:
    """invoices: list[StudentPeriodPayment]."""
    from bot.models.enums import PaymentStatus
    from bot.utils.dates import format_date_display  # noqa: F401 (used elsewhere)

    rows = []
    for p in invoices:
        icon = "✅" if p.status == PaymentStatus.PAID else "💸"
        label = f"{icon} {p.period_month} · {p.teacher_name or '—'} · {p.total_amount} ₽"
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f"client_invoice:{p.payment_id}",
        )])
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="client:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_client_invoice_view(
    payment_id: str, can_pay: bool, already_paid: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if already_paid:
        rows.append([InlineKeyboardButton(text="✅ Оплачено", callback_data="noop")])
    elif can_pay:
        rows.append([InlineKeyboardButton(
            text="💳 Оплатить", callback_data=f"client_pay:{payment_id}",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="🔴 Педагог не сдал период", callback_data="noop",
        )])
    rows.append([InlineKeyboardButton(text="« К счетам", callback_data="client:billing")])
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="client:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
