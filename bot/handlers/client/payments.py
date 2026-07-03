from __future__ import annotations
import asyncio
import json as _json
import logging

from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from bot.models.enums import PaymentStatus
from bot.repositories import UserRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.services import PaymentService

logger = logging.getLogger(__name__)
router = Router(name="client_payments")


async def _fetch_payment_from_api(payment_id: str):
    """Запрашивает платёж напрямую у API ЮКассы (server-to-server, по кредам магазина).

    Возвращает объект платежа (status/metadata/amount) или None, если ЮКасса
    не сконфигурирована. Синхронный SDK — в to_thread, чтобы не блокировать loop.
    """
    from yookassa import Configuration, Payment as YKPayment
    from config.settings import settings
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        return None
    Configuration.configure(settings.yookassa_shop_id, settings.yookassa_secret_key)
    return await asyncio.to_thread(YKPayment.find_one, payment_id)


async def process_yookassa_event(
    event: dict,
    payment_service: PaymentService,
    bot,
    user_repo: UserRepository,
    fetch_payment=_fetch_payment_from_api,
) -> int:
    """Обрабатывает событие webhook ЮКассы. Возвращает HTTP-статус ответа.

    Телу запроса НЕ доверяем (см. docs/FOUND_BUGS.md B4): из него берётся только
    object.id, после чего статус, metadata и сумма перепроверяются напрямую у API
    ЮКассы. Подтверждаем период только при реальном status == succeeded.
    200 — принято/проигнорировано; 500 — ЮКасса повторит уведомление позже.
    """
    if event.get("event") != "payment.succeeded":
        return 200

    payment_id = event.get("object", {}).get("id")
    if not payment_id:
        logger.warning("YooKassa webhook: нет object.id в теле — игнорируем")
        return 200

    try:
        payment = await fetch_payment(payment_id)
    except Exception as exc:
        logger.error("YooKassa webhook: не удалось проверить платёж %s через API: %s", payment_id, exc)
        return 500  # временная ошибка — пусть ЮКасса ретраит

    if payment is None:
        logger.warning("YooKassa webhook: платёж %s не найден/SDK не сконфигурирован — игнорируем", payment_id)
        return 200
    if getattr(payment, "status", None) != "succeeded":
        logger.warning(
            "YooKassa webhook: платёж %s имеет статус %r, не succeeded — игнорируем (возможна подделка)",
            payment_id, getattr(payment, "status", None),
        )
        return 200

    meta = getattr(payment, "metadata", None) or {}
    student_id = meta.get("student_id")
    period_month = meta.get("period_month")
    if not student_id or not period_month:
        logger.warning("YooKassa webhook: у платежа %s нет student_id/period_month в metadata", payment_id)
        return 200

    count = await payment_service.confirm_period(student_id, period_month, 0)
    if count > 0:
        amount = getattr(getattr(payment, "amount", None), "value", "?")
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
            except TelegramAPIError as exc:
                logger.warning("Не удалось уведомить админа об оплате tg_id=%s: %s", admin.tg_id, exc)

    return 200


def make_yookassa_webhook_handler(payment_service: PaymentService, bot, user_repo: UserRepository):
    """Фабрика aiohttp-обработчика для webhook ЮКасса."""
    async def handler(request: web.Request) -> web.Response:
        try:
            body = await request.read()
            event = _json.loads(body)
        except Exception:
            return web.Response(status=400)
        status = await process_yookassa_event(event, payment_service, bot, user_repo)
        return web.Response(status=status)
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
            except TelegramAPIError as exc:
                logger.warning("Не удалось уведомить админа об оплате tg_id=%s: %s", admin.tg_id, exc)
    else:
        logger.warning("confirm_payment вернул False для %s", payment_id)
        await message.answer("✅ Оплата получена! Спасибо.")
