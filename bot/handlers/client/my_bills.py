from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message

from dateutil.relativedelta import relativedelta  # type: ignore

from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import StudentRepository, UserRepository
from bot.services import PaymentService
from bot.states import ReceiptStates
from bot.utils.dates import display_period, format_date_display
from bot.keyboards.client import (
    kb_client_menu, kb_client_student_select, kb_bills_list,
    kb_bill_detail, kb_bill_back,
    kb_payment_method, kb_pay_cash, kb_pay_receipt, kb_cancel_receipt,
)
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router(name="client_bills")

_MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _period_label(period_month: str) -> str:
    year, month = period_month.split("-")
    return f"{_MONTHS_RU[int(month)]} {year}"


async def _show_bills(
    callback: CallbackQuery,
    students_all: list,
    student_id: str,
    payment_service: PaymentService,
) -> None:
    if student_id != "all":
        students = [s for s in students_all if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = students_all

    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]

    period_rows: list[tuple[str, str, str]] = []
    for period_month in periods:
        total = 0
        for student in students:
            bills = await payment_service.compute_bills_for_student_period(
                student.student_id, period_month,
            )
            total += sum(agg["total"] for agg in bills.values())

        label = _period_label(period_month)
        is_current = period_month == periods[0]

        if is_current:
            icon = "📅"
            suffix = f" — {total} руб. (текущий)" if total else " — нет занятий"
        else:
            paid = False
            for student in students:
                invoices = await payment_service.get_or_create_invoices_for_student_period(
                    student, period_month,
                )
                if invoices and all(inv.status == PaymentStatus.PAID for inv in invoices):
                    paid = True
                    break
            if total == 0:
                continue
            icon = "✅" if paid else "⏳"
            suffix = f" — {total} руб."

        period_rows.append((period_month, f"{label}{suffix}", icon))

    if not period_rows:
        await callback.message.edit_text(
            "📋 Занятий за последние 6 месяцев не найдено.",
            reply_markup=kb_client_menu(),
        )
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            "<b>📋 Счета</b>",
            reply_markup=kb_bills_list(period_rows, student_id),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "client:my_bills")
async def cb_my_bills(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if len(students) > 1:
        await callback.message.edit_text(
            "Выберите ученика:",
            reply_markup=kb_client_student_select(students, "bills"),
        )
        await callback.answer()
        return

    await _show_bills(callback, students, students[0].student_id, payment_service)


@router.callback_query(F.data.startswith("cl_bills_stu:"))
async def cb_cl_bills_stu(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_bills(callback, students, student_id, payment_service)


@router.callback_query(F.data.startswith("client_bill:"))
async def cb_bill_detail(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # client_bill:{student_id}:{period_month}
    _, student_id, period_month = callback.data.split(":", 2)

    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not all_students:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if student_id != "all":
        students = [s for s in all_students if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = all_students

    lines = [f"<b>📋 {_period_label(period_month)}</b>\n"]
    grand_total = 0
    unpaid_total = 0
    payment_ids: list[str] = []
    all_paid = True
    has_invoices = False

    for student in students:
        bills = await payment_service.compute_bills_for_student_period(
            student.student_id, period_month,
        )
        if not bills:
            continue

        invoices = await payment_service.get_or_create_invoices_for_student_period(
            student, period_month,
        )
        invoices_by_teacher = {inv.teacher_id: inv for inv in invoices}

        for inv in invoices:
            has_invoices = True
            payment_ids.append(inv.payment_id)
            if inv.status != PaymentStatus.PAID:
                all_paid = False

        if len(students) > 1:
            lines.append(f"<b>{student.name}:</b>")

        for teacher_id, agg in bills.items():
            inv = invoices_by_teacher.get(teacher_id)
            teacher_paid = inv is not None and inv.status == PaymentStatus.PAID

            teacher_name = agg["name"] or teacher_id
            paid_mark = " ✅" if teacher_paid else ""
            lines.append(f"<b>Педагог: {teacher_name}{paid_mark}</b>")

            for item in sorted(agg["items"], key=lambda b: b.date):
                lines.append(
                    f"  {format_date_display(item.date)}  {item.duration_min} мин  — {item.amount} руб."
                )

            if teacher_paid:
                lines.append(f"  <i>Итого: {agg['total']} руб. — оплачено</i>\n")
            else:
                lines.append(f"  <i>Итого: {agg['total']} руб.</i>\n")
                unpaid_total += agg["total"]

            grand_total += agg["total"]

    if grand_total == 0:
        await callback.message.edit_text(
            f"📋 {_period_label(period_month)}\n\nЗанятий не найдено.",
            reply_markup=kb_bill_detail([], False, period_month, student_id),
        )
        await callback.answer()
        return

    if unpaid_total > 0:
        lines.append(f"<b>К оплате: {unpaid_total} руб.</b>")
    elif has_invoices and all_paid:
        lines.append("✅ Период полностью оплачен")

    can_pay = has_invoices and not all_paid and unpaid_total > 0

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_bill_detail(payment_ids if can_pay else [], can_pay, period_month, student_id),
    )
    await callback.answer()


_METHOD_LABELS = {
    "cash": "💵 Наличные",
    "bank": "🏦 По реквизитам",
    "sbp":  "📱 СБП",
}


async def _get_student_and_total(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    student_id: str,
    period_month: str,
) -> tuple | None:
    """Возвращает (student, unpaid_total) или None если нет доступа/неоплаченных начислений."""
    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    student = next((s for s in all_students if s.student_id == student_id), None)
    if not student:
        await callback.answer("Нет доступа", show_alert=True)
        return None
    bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
    invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
    paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
    total = sum(agg["total"] for tid, agg in bills.items() if tid not in paid_teachers)
    if total == 0:
        await callback.answer("Нет неоплаченных начислений", show_alert=True)
        return None
    return student, total


@router.callback_query(F.data.startswith("client_pay:"))
async def cb_client_pay(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # client_pay:{student_id}:{period_month}
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, student_id, period_month = parts

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, total = result

    invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
    if invoices and all(inv.status == PaymentStatus.PAID for inv in invoices):
        await callback.answer("Этот счёт уже оплачен", show_alert=True)
        return

    text = (
        f"<b>💳 Оплата за {_period_label(period_month)}</b>\n"
        f"Сумма: <b>{total} руб.</b>\n\n"
        f"Выберите способ оплаты:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=kb_payment_method(student_id, period_month, cash=True, bank=True, sbp=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_method:"))
async def cb_pay_method(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # pay_method:{method}:{student_id}:{period_month}
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, method, student_id, period_month = parts

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    _student, total = result

    if method == "cash":
        text = (
            f"<b>💵 Оплата наличными</b>\n"
            f"Сумма: <b>{total} руб.</b>\n\n"
            f"Передайте деньги администратору или преподавателю.\n"
            f"Нажмите кнопку, чтобы уведомить администратора."
        )
        await callback.message.edit_text(text, reply_markup=kb_pay_cash(student_id, period_month))

    elif method == "bank":
        lines = [
            f"<b>🏦 Оплата по реквизитам</b>",
            f"Сумма: <b>{total} руб.</b>",
            "",
        ]
        if settings.payment_bank_details:
            lines += [settings.payment_bank_details.replace("\\n", "\n"), ""]
        lines.append("После оплаты прикрепите фото чека.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb_pay_receipt(method, student_id, period_month),
        )
        # Отправляем QR-код
        qr_sent = False
        if settings.payment_qr_data:
            try:
                from io import BytesIO
                import qrcode
                img = qrcode.make(settings.payment_qr_data)
                buf = BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                from aiogram.types import BufferedInputFile
                await callback.message.answer_photo(
                    BufferedInputFile(buf.read(), filename="qr.png"),
                    caption="QR-код для оплаты",
                )
                qr_sent = True
            except Exception as exc:
                logger.warning("Не удалось сгенерировать QR: %s", exc)
        if not qr_sent and settings.payment_qr_image_url:
            try:
                await callback.message.answer_photo(
                    settings.payment_qr_image_url,
                    caption="QR-код для оплаты",
                )
            except Exception as exc:
                logger.warning("Не удалось отправить QR-код: %s", exc)

    elif method == "sbp":
        lines = [
            f"<b>📱 Оплата через СБП</b>",
            f"Сумма: <b>{total} руб.</b>",
            "",
        ]
        if settings.payment_sbp_details:
            lines += [settings.payment_sbp_details, ""]
        lines.append("После оплаты прикрепите фото чека.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb_pay_receipt(method, student_id, period_month),
        )

    await callback.answer()


@router.callback_query(F.data.startswith("cash_notify:"))
async def cb_cash_notify(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    user_repo: UserRepository,
) -> None:
    _, student_id, period_month = callback.data.split(":", 2)

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, total = result

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Подтвердить оплату",
            callback_data=f"receipt_confirm:{student_id}:{period_month}",
        ),
    ]])
    msg = (
        f"💵 Клиент сообщает об оплате наличными\n\n"
        f"Ученик: {student.name}\n"
        f"Период: {_period_label(period_month)}\n"
        f"Сумма: {total} руб."
    )
    admins = await user_repo.get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(admin.tg_id, msg, reply_markup=confirm_kb)
        except Exception:
            pass

    await callback.message.edit_text(
        "✅ Администратор уведомлён. Ожидайте подтверждения.",
        reply_markup=kb_bill_back(student_id, period_month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("receipt_upload:"))
async def cb_receipt_upload(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    # receipt_upload:{method}:{student_id}:{period_month}
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, method, student_id, period_month = parts

    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not any(s.student_id == student_id for s in all_students):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(ReceiptStates.waiting_for_receipt)
    await state.update_data(
        receipt_method=method,
        receipt_student_id=student_id,
        receipt_period_month=period_month,
    )
    await callback.message.edit_text(
        "📎 Отправьте фото или документ чека об оплате:",
        reply_markup=kb_cancel_receipt(student_id, period_month),
    )
    await callback.answer()


@router.message(ReceiptStates.waiting_for_receipt, F.photo | F.document)
async def on_receipt_photo(
    message: Message,
    state: FSMContext,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    user_repo: UserRepository,
) -> None:
    data = await state.get_data()
    student_id = data["receipt_student_id"]
    period_month = data["receipt_period_month"]
    method = data.get("receipt_method", "bank")
    await state.clear()

    students = await student_repo.get_by_parent_tg_id(message.from_user.id)
    student = next((s for s in students if s.student_id == student_id), None)
    student_name = student.name if student else student_id

    bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
    if student:
        invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
        paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
        total = sum(agg["total"] for tid, agg in bills.items() if tid not in paid_teachers)
    else:
        total = sum(agg["total"] for agg in bills.values())
    method_label = _METHOD_LABELS.get(method, method)

    caption = (
        f"📎 Чек об оплате\n\n"
        f"Способ: {method_label}\n"
        f"Ученик: {student_name}\n"
        f"Период: {_period_label(period_month)}\n"
        f"Сумма: {total} руб."
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Подтвердить оплату",
            callback_data=f"receipt_confirm:{student_id}:{period_month}",
        ),
    ]])

    admins = await user_repo.get_admins()
    for admin in admins:
        try:
            if message.photo:
                await message.bot.send_photo(
                    admin.tg_id, message.photo[-1].file_id,
                    caption=caption, reply_markup=confirm_kb,
                )
            else:
                await message.bot.send_document(
                    admin.tg_id, message.document.file_id,
                    caption=caption, reply_markup=confirm_kb,
                )
        except Exception:
            pass

    await message.answer(
        "✅ Чек отправлен администратору. Ожидайте подтверждения.",
        reply_markup=kb_bill_back(student_id, period_month),
    )


@router.callback_query(F.data.startswith("receipt_confirm:"))
async def cb_receipt_confirm(
    callback: CallbackQuery,
    user: User | None,
    payment_service: PaymentService,
) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)
    count = await payment_service.confirm_period(student_id, period_month, callback.from_user.id)
    if count > 0:
        old_text = callback.message.caption or callback.message.text or ""
        confirmed_suffix = "\n\n✅ Оплата подтверждена"
        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(
                    caption=old_text + confirmed_suffix,
                    reply_markup=None,
                )
            else:
                await callback.message.edit_text(
                    old_text + confirmed_suffix,
                    reply_markup=None,
                )
        except Exception:
            pass
        await callback.answer("Оплата подтверждена")
    else:
        await callback.answer("Счёт уже подтверждён или не найден", show_alert=True)
