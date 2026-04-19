from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import PaymentRepository
from bot.services import PaymentService
from bot.keyboards import kb_client_invoices, kb_client_invoice_view
from bot.utils.dates import display_period, format_date_display

logger = logging.getLogger(__name__)
router = Router(name="client_billing")


def _is_client(user: User | None) -> bool:
    return user is not None and bool(user.student_id)


@router.callback_query(F.data == "client:billing")
async def cb_client_billing(
    callback: CallbackQuery,
    user: User | None,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    invoices = await payment_repo.get_by_student(user.student_id)
    invoices.sort(key=lambda p: (p.period_month, p.teacher_name or ""), reverse=True)
    if not invoices:
        await callback.message.edit_text(
            "💳 <b>Мои счета</b>\n\nПока нет выставленных счетов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« В меню", callback_data="client:menu")],
            ]),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "💳 <b>Мои счета</b>\n\nВыберите счёт:",
        reply_markup=kb_client_invoices(invoices),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_invoice:"))
async def cb_client_invoice_detail(
    callback: CallbackQuery,
    user: User | None,
    payment_repo: PaymentRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    payment_id = callback.data.split(":", 1)[1]
    invoice = next(
        (p for p in await payment_repo.get_by_student(user.student_id)
         if p.payment_id == payment_id),
        None,
    )
    if invoice is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return

    bills = await payment_service.compute_bills_for_student_period(
        user.student_id, invoice.period_month,
    )
    agg = bills.get(invoice.teacher_id, {"items": [], "total": invoice.total_amount})

    not_submitted = await payment_service.teachers_not_submitted(
        [invoice.teacher_id], invoice.period_month,
    )
    teacher_not_submitted = bool(not_submitted)
    already_paid = invoice.status == PaymentStatus.PAID

    lines = [
        f"💳 <b>Счёт {invoice.payment_id}</b>",
        f"Период: {display_period(invoice.period_month)}",
        f"Педагог: {invoice.teacher_name or '—'}",
        "",
    ]
    for b in agg["items"]:
        lines.append(f"  {format_date_display(b.date)} · {b.duration_min} мин · {b.amount} ₽")
    if not agg["items"]:
        lines.append("  (детализация недоступна)")
    lines.append("")
    lines.append(f"<b>Итого: {invoice.total_amount} ₽</b>")
    if already_paid:
        lines.append(f"\n✅ Оплачено: {invoice.paid_at or ''}")
    elif teacher_not_submitted:
        lines.append("\n🔴 Педагог ещё не сдал период — оплата будет доступна позже.")
    else:
        lines.append("\n⏳ Ожидает оплаты")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_client_invoice_view(
            payment_id=invoice.payment_id,
            can_pay=not teacher_not_submitted and not already_paid,
            already_paid=already_paid,
        ),
    )
    await callback.answer()
