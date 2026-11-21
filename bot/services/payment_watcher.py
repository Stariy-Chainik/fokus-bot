"""Автоподтверждение платежа ЮКассы опросом статуса (fallback без HTTPS-вебхука).

После создания платежа запускается фоновая задача: раз в `interval` секунд
статус перепроверяется напрямую у API ЮКассы (по кредам магазина — телу
клиента не доверяем, та же модель, что у вебхука). На `succeeded` — счета
периода зачитываются через общий `settle_yookassa_payment` (идемпотентно по
`payment_id` ЮКассы и под общим `payment_lock` с ручными отметками), затем
уведомляются админы (Telegram) и плательщик (по адресу `parent_addr`).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import AsyncExitStack
from datetime import date, timedelta
import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError

from bot.services import activity
from bot.services.parent_notifier import resolve_notifier
from bot.services.payment_methods import yookassa_method
from bot.services.payment_service import _YK_PAYMENT_REF, payment_lock

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 20
_MAX_CHECKS = 45  # ~15 минут
_SETTLED_CACHE_MAX = 2000
_DEDUP_LOOKBACK_DAYS = 30
_tasks: set[asyncio.Task] = set()


def _to_int(value) -> int:
    try:
        return int(round(float(str(value))))
    except (TypeError, ValueError):
        return 0


def _settled_store(payment_service: Any) -> OrderedDict[str, None]:
    store = getattr(payment_service, "_yk_settled_ids", None)
    if store is None:
        store = OrderedDict()
        payment_service._yk_settled_ids = store
    return store


def _yk_lock(payment_service: Any, payment_id: str) -> asyncio.Lock:
    locks: dict[str, asyncio.Lock] | None = getattr(payment_service, "_yk_locks", None)
    if locks is None:
        locks = {}
        payment_service._yk_locks = locks
    if len(locks) > 512:
        for key in [k for k, lk in locks.items() if not lk.locked() and k != payment_id]:
            locks.pop(key, None)
    return locks.setdefault(payment_id, asyncio.Lock())


def _mark_settled(payment_service: Any, payment_id: str) -> None:
    if not payment_id:
        return
    store = _settled_store(payment_service)
    store[payment_id] = None
    store.move_to_end(payment_id)
    while len(store) > _SETTLED_CACHE_MAX:
        store.popitem(last=False)


async def _already_in_activity(payment_id: str) -> bool:
    """Проверка после рестарта: не был ли этот платёж ЮКассы уже записан в activity_log."""
    repo = activity._repo
    if repo is None or not payment_id:
        return False
    marker = f"yk:{payment_id}"
    since = (date.today() - timedelta(days=_DEDUP_LOOKBACK_DAYS)).isoformat()
    try:
        events = await repo.since(since)
    except Exception as exc:
        logger.warning("Проверка дубля ЮКассы %s в activity_log не удалась: %s", payment_id, exc)
        return False
    return any(marker in (e.ref or "").split() for e in events)


async def settle_yookassa_payment(
    payment_id: str,
    payment: Any,
    payment_service: Any,
    *,
    student_id: str,
    student_name: str,
    period_month: str,
    amount: int,
    teacher_ids: list[str] | None = None,
    lesson_ids: list[str] | None = None,
    periods: list[str] | None = None,
) -> tuple[int, int]:
    """Идемпотентный зачёт подтверждённого платежа ЮКассы (общий для webhook и watcher).

    Защищает от двойного зачёта («переплата») при повторе вебхука или одновременном
    срабатывании webhook + watcher, а также берёт общий `payment_lock` с ручной отметкой.
    """
    target_periods = sorted(set(periods)) if periods and len(periods) >= 2 else [period_month]
    method = yookassa_method(payment)

    async def _apply() -> tuple[int, int]:
        if payment_id and payment_id in _settled_store(payment_service):
            logger.info("ЮКасса %s уже зачтён в текущем процессе — пропускаем повтор", payment_id)
            return 0, 0
        if await _already_in_activity(payment_id):
            _mark_settled(payment_service, payment_id)
            logger.info("ЮКасса %s уже зачтён ранее (activity_log) — пропускаем повтор", payment_id)
            return 0, 0
        async with AsyncExitStack() as stack:
            for ym in target_periods:
                await stack.enter_async_context(payment_lock(student_id, ym))
            token = _YK_PAYMENT_REF.set(payment_id or "")
            try:
                if len(target_periods) >= 2:
                    credited, count = await payment_service.record_payment_periods(
                        student_id, student_name, target_periods, amount, 0, "ЮКасса", method,
                    )
                elif lesson_ids:
                    credited, count = await payment_service.record_payment(
                        student_id, student_name, period_month, amount, 0,
                        teacher_ids or None, "ЮКасса", method, lesson_ids=lesson_ids,
                    )
                else:
                    credited, count = await payment_service.record_payment(
                        student_id, student_name, period_month, amount, 0,
                        teacher_ids or None, "ЮКасса", method,
                    )
            finally:
                _YK_PAYMENT_REF.reset(token)
        _mark_settled(payment_service, payment_id)
        return credited, count

    if payment_id:
        async with _yk_lock(payment_service, payment_id):
            return await _apply()
    return await _apply()


async def _fetch(payment_id: str):
    from yookassa import Configuration, Payment as YKPayment
    from config.settings import settings
    Configuration.configure(settings.yookassa_shop_id, settings.yookassa_secret_key)
    return await asyncio.to_thread(YKPayment.find_one, payment_id)


async def _watch(
    payment_id: str, student_id: str, student_name: str, period_month: str,
    payment_service, bot, user_repo, parent_addr=None,
    interval: int = _INTERVAL_SEC, max_checks: int = _MAX_CHECKS, fetch=_fetch,
    teacher_ids: list | None = None, notifier=None, periods: list | None = None,
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

        amount = getattr(getattr(payment, "amount", None), "value", "?")
        meta = getattr(payment, "metadata", None) or {}
        lesson_ids = [x for x in (meta.get("lesson_ids") or "").split("|") if x]
        credited, count = await settle_yookassa_payment(
            payment_id, payment, payment_service,
            student_id=student_id, student_name=student_name, period_month=period_month,
            amount=_to_int(amount), teacher_ids=teacher_ids, lesson_ids=lesson_ids or None,
            periods=periods,
        )
        if periods and len(periods) >= 2:
            period_month = ", ".join(sorted(periods))
        logger.info(
            "Платёж %s succeeded (опрос): student=%s period=%s зачтено=%d подтверждено счетов=%d",
            payment_id, student_id, period_month, credited, count,
        )
        if count > 0:
            if parent_addr:
                await (notifier or resolve_notifier(bot)).send(
                    parent_addr,
                    f"✅ Оплата получена! {student_name}, {period_month} — {amount} руб. Спасибо!",
                )
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
    payment_service, bot, user_repo, parent_addr=None,
    interval: int = _INTERVAL_SEC, max_checks: int = _MAX_CHECKS, fetch=_fetch,
    teacher_ids: list | None = None, notifier=None, periods: list | None = None,
) -> asyncio.Task:
    """Запускает фоновый опрос платежа; сильная ссылка на задачу хранится до завершения."""
    task = asyncio.create_task(_watch(
        payment_id, student_id, student_name, period_month,
        payment_service, bot, user_repo, parent_addr, interval, max_checks, fetch,
        teacher_ids, notifier, periods,
    ))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task
