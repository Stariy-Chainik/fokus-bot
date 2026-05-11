from __future__ import annotations
import json as _json
import logging

from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiohttp import web

from bot.models.enums import PaymentStatus
from bot.repositories import UserRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.services import PaymentService

logger = logging.getLogger(__name__)
router = Router(name="client_payments")


def make_yookassa_webhook_handler(payment_service: PaymentService, bot, user_repo: UserRepository):
    """Фабрика aiohttp-обработчика для webhook ЮКасса."""
    async def handler(request: web.Request) -> web.Response:
        try:
            body = await request.read()
            event = _json.loads(body)
        except Exception:
            return web.Response(status=400)

        if event.get("event") != "payment.succeeded":
            return web.Response(status=200)

        meta = event.get("object", {}).get("metadata", {})
        student_id = meta.get("student_id")
        period_month = meta.get("period_month")
        if not student_id or not period_month:
            logger.warning("YooKassa webhook: нет student_id/period_month в metadata")
            return web.Response(status=200)

        count = await payment_service.confirm_period(student_id, period_month, 0)
        if count > 0:
            amount = event.get("object", {}).get("amount", {}).get("value", "?")
            msg = (
                f"💰 Оплата через ЮКасса\n\n"
                f"Ученик: {student_id}\n"
                f"Период: {period_month}\n"
                f"Сумма: {amount} руб."
            )
            admins = await user_repo.get_admins()
            for admin in admins:
                try:
                    await bot.send_message(admin.tg_id, msg)
                except Exception:
                    pass

        return web.Response(status=200)
    return handler


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
