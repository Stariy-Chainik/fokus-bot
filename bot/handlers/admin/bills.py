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
    for b in billing_rows:
        by_teacher[b.teacher_name].append(b)

    payment = await payment_repo.get_by_student_and_period(student_id, period_month)
    if payment and payment.status.value == "paid":
        status_text = f"✅ Оплачен ({payment.paid_at or ''})"
    elif payment:
        status_text = "📋 Счёт создан, ожидает оплаты"
    else:
        status_text = "⏳ Не оплачен"

    lines = [f"<b>Счёт: {student.name}</b>", f"Период: {display_period(period_month)}", f"Статус: {status_text}", ""]
    total = 0
    for teacher_name, rows in by_teacher.items():
        lines.append(f"👨‍🏫 {teacher_name}:")
        for b in rows:
            lines.append(f"  {format_date_display(b.date)} | {b.duration_min} мин | {b.amount} руб.")
            total += b.amount
        lines.append("")
    lines.append(f"Итого: {total} руб.")

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
async def cb_pay_confirm(
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

    payment = await payment_service.get_or_create_invoice(student, period_month)

    if payment.status.value == "paid":
        await callback.message.edit_text(
            f"Счёт {payment.payment_id} уже оплачен.", reply_markup=kb_back("admin:bills")
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<b>Подтвердить оплату счёта {payment.payment_id}?</b>\n"
        f"Ученик: {student.name}\n"
        f"Период: {display_period(period_month)}\n"
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
