from __future__ import annotations
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message


from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import StudentRepository, UserRepository
from bot.repositories.client_repo import ClientRepository
from bot.services import PaymentService
from bot.services.cloudkassir_service import CloudKassirService
from bot.states import ReceiptStates
from bot.keyboards.client import (
    kb_bill_back, kb_payment_method, kb_pay_cash,
    kb_pay_receipt, kb_cancel_receipt,
)
from config.settings import settings

logger = logging.getLogger(__name__)

from ._base import router, _period_label


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
        reply_markup=kb_payment_method(
            student_id, period_month,
            cash=False, bank=True, sbp=False,
            yookassa=bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        ),
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

    elif method == "yookassa":
        student = _student
        try:
            url = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total,
            )
            await callback.message.edit_text(
                f"<b>💳 Оплата картой онлайн</b>\n"
                f"Сумма: <b>{total} руб.</b>\n\n"
                f"Нажмите кнопку для перехода на страницу оплаты.\n"
                f"После оплаты статус обновится автоматически.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
                    [InlineKeyboardButton(text="« К счёту", callback_data=f"client_bill:{student_id}:{period_month}")],
                ]),
            )
        except Exception as exc:
            logger.error("Ошибка создания платежа ЮКасса: %s", exc)
            await callback.answer("Ошибка при создании платежа. Попробуйте другой способ.", show_alert=True)
            return

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
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа о наличной оплате tg_id=%s: %s", admin.tg_id, exc)

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
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить чек админу tg_id=%s: %s", admin.tg_id, exc)

    await message.answer(
        "✅ Чек отправлен администратору. Ожидайте подтверждения.",
        reply_markup=kb_bill_back(student_id, period_month),
    )


@router.callback_query(F.data.startswith("receipt_confirm:"))
async def cb_receipt_confirm(
    callback: CallbackQuery,
    user: User | None,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
    cloudkassir_service: CloudKassirService,
) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)

    # Получаем сумму к подтверждению до confirm (после — статус уже PAID)
    student = await student_repo.get_by_id(student_id)
    pending_total = 0
    if student:
        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
        paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
        pending_total = sum(agg["total"] for tid, agg in bills.items() if tid not in paid_teachers)

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
        except TelegramBadRequest:
            pass
        await callback.answer("Оплата подтверждена")

        # Фискальный чек через CloudKassir
        if student and pending_total > 0 and cloudkassir_service._public_id:
            phone = None
            if student.client_id:
                client = await client_repo.get_by_id(student.client_id)
                phone = client.phone if client else None
            if phone:
                await cloudkassir_service.send_income_receipt(
                    phone, student.name, period_month, pending_total,
                )
            else:
                logger.warning(
                    "CloudKassir: нет телефона для student=%s, чек не выбит", student_id,
                )
    else:
        await callback.answer("Счёт уже подтверждён или не найден", show_alert=True)
