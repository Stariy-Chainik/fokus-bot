from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery

from bot.models.enums import PaymentStatus
from bot.repositories import UserRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.services import PaymentService

logger = logging.getLogger(__name__)
router = Router(name="client_payments")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, payment_service: PaymentService) -> None:
    payment_repo: PaymentRepository = payment_service._payment_repo
    payment = await payment_repo.get_by_id(query.invoice_payload)
    if not payment:
        await query.answer(ok=False, error_message="Счёт не найден")
        return
    if payment.status == PaymentStatus.PAID:
        await query.answer(ok=False, error_message="Счёт уже оплачен")
        return
    if payment.total_amount * 100 != query.total_amount:
        await query.answer(ok=False, error_message="Сумма изменилась, обновите счёт")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    payment_service: PaymentService,
    user_repo: UserRepository,
) -> None:
    payment_id = message.successful_payment.invoice_payload
    ok = await payment_service.confirm_payment(payment_id, message.from_user.id)
    if ok:
        await message.answer("✅ Оплата получена! Спасибо.")
        payment_repo: PaymentRepository = payment_service._payment_repo
        payment = await payment_repo.get_by_id(payment_id)
        admins = await user_repo.get_admins()
        student_name = payment.student_name if payment else "—"
        period = payment.period_month if payment else "—"
        amount = payment.total_amount if payment else "—"
        notify = (
            f"💰 Получена оплата от клиента\n\n"
            f"Ученик: {student_name}\n"
            f"Период: {period}\n"
            f"Сумма: {amount} руб.\n"
            f"ID счёта: {payment_id}"
        )
        for admin in admins:
            try:
                await message.bot.send_message(admin.tg_id, notify)
            except Exception:
                pass
    else:
        logger.warning("confirm_payment вернул False для %s", payment_id)
        await message.answer("✅ Оплата получена! Спасибо.")
