from __future__ import annotations
import logging
from collections import defaultdict
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import StudentRepository, BillingRepository, PaymentRepository
from bot.services import PaymentService
from bot.keyboards.admin import kb_bills_menu, kb_student_list, kb_back, kb_confirm
from bot.utils.dates import display_period, format_date_display

logger = logging.getLogger(__name__)
router = Router(name="admin_bills")

# Технический guard от двойного нажатия «Подтвердить»
_confirming_in_progress: set[str] = set()


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _period_buttons(student_id: str, action_prefix: str) -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{student_id}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:bills")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin:bills")
async def cb_bills_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("<b>Счета учеников:</b>", reply_markup=kb_bills_menu())
    await callback.answer()


# ─── Просмотр счёта ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "bills:view")
async def cb_bills_choose_student(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    students = await student_repo.get_all()
    if not students:
        await callback.message.edit_text("Учеников нет.", reply_markup=kb_back("admin:bills"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите ученика:</b>", reply_markup=kb_student_list(students, "bill_student", back_cb="admin:bills")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bill_student:"))
async def cb_bills_choose_period(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await callback.message.edit_text("<b>Выберите период:</b>", reply_markup=_period_buttons(student_id, "bill_period"))
    await callback.answer()


@router.callback_query(F.data.startswith("bill_period:"))
async def cb_bills_show(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    billing_repo: BillingRepository,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    billing_rows = await billing_repo.get_by_student_and_period(student_id, period_month)
    if not billing_rows:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет занятий.",
            reply_markup=kb_back("admin:bills"),
        )
        await callback.answer()
        return

    by_teacher: dict[str, list] = defaultdict(list)
    teacher_names: dict[str, str] = {}
    for b in billing_rows:
        by_teacher[b.teacher_id].append(b)
        teacher_names[b.teacher_id] = b.teacher_name

    payments = await payment_repo.get_by_student_and_period(student_id, period_month)
    pay_by_teacher = {p.teacher_id: p for p in payments}

    lines = [f"<b>Счёт: {student.name}</b>", f"Период: {display_period(period_month)}", ""]
    grand_total = 0
    for teacher_id, rows in by_teacher.items():
        subtotal = sum(b.amount for b in rows)
        grand_total += subtotal
        p = pay_by_teacher.get(teacher_id)
        if p and p.status.value == "paid":
            status = f"✅ Оплачен ({p.paid_at or ''})"
        elif p:
            status = "📋 Ожидает оплаты"
        else:
            status = "⏳ Счёт не создан"
        lines.append(f"👨‍🏫 <b>{teacher_names[teacher_id]}</b> — {subtotal} руб. — {status}")
        for b in rows:
            lines.append(f"  {format_date_display(b.date)} | {b.duration_min} мин | {b.amount} руб.")
        lines.append("")
    lines.append(f"Итого: {grand_total} руб.")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb_back("admin:bills"))
    await callback.answer()


# ─── Подтверждение оплаты ─────────────────────────────────────────────────────

@router.callback_query(F.data == "bills:confirm_payment")
async def cb_confirm_payment_start(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    students = await student_repo.get_all()
    if not students:
        await callback.message.edit_text("Учеников нет.", reply_markup=kb_back("admin:bills"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите ученика:</b>", reply_markup=kb_student_list(students, "pay_student", back_cb="admin:bills")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_student:"))
async def cb_pay_choose_period(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await callback.message.edit_text("<b>Выберите период:</b>", reply_markup=_period_buttons(student_id, "pay_period"))
    await callback.answer()


@router.callback_query(F.data.startswith("pay_period:"))
async def cb_pay_pick_invoice(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    invoices = await payment_service.get_or_create_invoices_for_student_period(
        student, period_month,
    )
    if not invoices:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет занятий.",
            reply_markup=kb_back("admin:bills"),
        )
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    for p in invoices:
        paid = p.status.value == "paid"
        icon = "✅" if paid else "⏳"
        label = f"{icon} {p.teacher_name or '—'} — {p.total_amount} руб."
        if paid:
            rows.append([InlineKeyboardButton(text=label, callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"pay_invoice:{p.payment_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:bills")])

    await callback.message.edit_text(
        f"<b>{student.name}</b> — {display_period(period_month)}\n"
        "Выберите счёт для подтверждения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_invoice:"))
async def cb_pay_confirm(
    callback: CallbackQuery, user: User | None,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    payment_id = callback.data.split(":", 1)[1]
    payment = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id), None,
    )
    if not payment:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    if payment.status.value == "paid":
        await callback.message.edit_text(
            f"Счёт {payment.payment_id} уже оплачен.", reply_markup=kb_back("admin:bills"),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"<b>Подтвердить оплату счёта {payment.payment_id}?</b>\n"
        f"Ученик: {payment.student_name}\n"
        f"Педагог: {payment.teacher_name or '—'}\n"
        f"Период: {display_period(payment.period_month)}\n"
        f"Сумма: {payment.total_amount} руб.",
        reply_markup=kb_confirm(f"do_confirm_payment:{payment.payment_id}", "admin:bills"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_confirm_payment:"))
async def cb_do_confirm_payment(
    callback: CallbackQuery, user: User | None, payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    payment_id = callback.data.split(":", 1)[1]

    # Технический guard от двойного нажатия
    if payment_id in _confirming_in_progress:
        logger.warning("Двойное подтверждение payment_id=%s tg_id=%s", payment_id, callback.from_user.id)
        await callback.answer("Оплата уже обрабатывается", show_alert=True)
        return
    _confirming_in_progress.add(payment_id)

    try:
        ok = await payment_service.confirm_payment(payment_id, callback.from_user.id)
        if ok:
            await callback.message.edit_text(f"Оплата {payment_id} подтверждена.", reply_markup=kb_back("admin:bills"))
        else:
            await callback.message.edit_text("Счёт уже оплачен или не найден.", reply_markup=kb_back("admin:bills"))
    except Exception as exc:
        logger.error("Ошибка подтверждения оплаты %s: %s", payment_id, exc)
        await callback.message.edit_text("Ошибка при подтверждении оплаты.", reply_markup=kb_back("admin:bills"))
    finally:
        _confirming_in_progress.discard(payment_id)

    await callback.answer()
