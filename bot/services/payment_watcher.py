"""Автоподтверждение платежа ЮКассы опросом статуса (fallback без HTTPS-вебхука).

После создания платежа запускается фоновая задача: раз в `interval` секунд
статус перепроверяется напрямую у API ЮКассы (по кредам магазина — телу
клиента не доверяем, та же модель, что у вебхука). На `succeeded` — счета
периода помечаются PAID, уведомляются админы и плательщик. Идемпотентно
с вебхуком: повторное подтверждение ничего не меняет (PENDING уже нет).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 20
_MAX_CHECKS = 45  # ~15 минут


async def _fetch(payment_id: str):
    from yookassa import Configuration, Payment as YKPayment
    from config.settings import settings
    Configuration.configure(settings.yookassa_shop_id, settings.yookassa_secret_key)
    return await asyncio.to_thread(YKPayment.find_one, payment_id)


async def _watch(
    payment_id: str, student_id: str, student_name: str, period_month: str,
    payment_service, bot, user_repo, parent_tg_id: int | None,
    interval: int, max_checks: int, fetch=_fetch, teacher_ids: list | None = None,
) -> None:
    for _ in range(max_checks):
        await asyncio.sleep(interval)
        try:
            payment = await fetch(payment_id)
        except Exception as exc:
            logger.warning("Опрос платежа %s: ошибка API (%s), продолжаем", payment_id, exc)
            continue
        status = getattr(payment, "status", None)
        if status == "canceled":
            logger.info("Платёж %s отменён/истёк — опрос остановлен", payment_id)
            return
        if status != "succeeded":
            continue

        if teacher_ids:
            count = await payment_service.confirm_teachers(student_id, period_month, teacher_ids, 0)
        else:
            count = await payment_service.confirm_period(student_id, period_month, 0)
        amount = getattr(getattr(payment, "amount", None), "value", "?")
        logger.info(
            "Платёж %s succeeded (опрос): student=%s period=%s подтверждено счетов=%d",
            payment_id, student_id, period_month, count,
        )
        if count > 0:
            if parent_tg_id:
                try:
                    await bot.send_message(
                        parent_tg_id,
                        f"✅ Оплата получена! {student_name}, "
                        f"{period_month} — {amount} руб. Спасибо!",
                    )
                except TelegramAPIError:
                    pass
            msg = (
                f"💰 Оплата через ЮКасса\n\n"
                f"Ученик: {student_name} ({student_id})\n"
                f"Период: {period_month}\n"
                f"Сумма: {amount} руб."
            )
            for admin in await user_repo.get_admins():
                try:
                    await bot.send_message(admin.tg_id, msg)
                except TelegramAPIError as exc:
                    logger.warning("Не удалось уведомить админа tg_id=%s: %s", admin.tg_id, exc)
        return
    logger.info("Платёж %s: опрос завершён без succeeded (истекло время)", payment_id)


def start_payment_watch(
    payment_id: str, student_id: str, student_name: str, period_month: str,
    payment_service, bot, user_repo, parent_tg_id: int | None = None,
    interval: int = _INTERVAL_SEC, max_checks: int = _MAX_CHECKS, fetch=_fetch,
    teacher_ids: list | None = None,
) -> asyncio.Task:
    """Запускает фоновый опрос платежа; задача живёт в текущем event loop."""
    return asyncio.create_task(_watch(
        payment_id, student_id, student_name, period_month,
        payment_service, bot, user_repo, parent_tg_id, interval, max_checks, fetch,
        teacher_ids,
    ))
