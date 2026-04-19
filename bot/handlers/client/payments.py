from __future__ import annotations
import json
import logging

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message, LabeledPrice, PreCheckoutQuery,
)

from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import PaymentRepository, UserRepository
from bot.services import PaymentService
from bot.utils.dates import display_period
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router(name="client_payments")


def _is_client(user: User | None) -> bool:
    return user is not None and bool(user.student_id)


def _build_provider_data(total_rub: int, period_month: str) -> str:
    """Формирует чек 54-ФЗ для ЮKassa.

    vat_code=1 (без НДС) — упрощёнка; при другой системе налогообложения
    значение нужно согласовать с бухгалтером (6 = НДС 20%).
    """
    return json.dumps({
        "receipt": {
            "items": [{
                "description": f"Услуги обучения танцами {display_period(period_month)}",
                "quantity": "1.00",
                "amount": {"value": f"{total_rub}.00", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        },
    })


@router.callback_query(F.data.startswith("client_pay:"))
async def cb_client_pay(
    callback: CallbackQuery,
    user: User | None,
    payment_repo: PaymentRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    if not settings.yookassa_provider_token:
        await callback.answer("Оплата временно недоступна", show_alert=True)
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
    if invoice.status == PaymentStatus.PAID:
        await callback.answer("Счёт уже оплачен", show_alert=True)
        return

    not_submitted = await payment_service.teachers_not_submitted(
        [invoice.teacher_id], invoice.period_month,
    )
    if not_submitted:
        await callback.answer(
            "Педагог ещё не сдал период — оплата будет доступна позже.",
            show_alert=True,
        )
        return

    try:
        await callback.message.answer_invoice(
            title=f"Танцы «Фокус» — {display_period(invoice.period_month)}",
            description=f"Педагог: {invoice.teacher_name or '—'}",
            payload=invoice.payment_id,
            provider_token=settings.yookassa_provider_token,
            currency="RUB",
            prices=[LabeledPrice(
                label="Оплата занятий",
                amount=invoice.total_amount * 100,  # в копейках
            )],
            provider_data=_build_provider_data(invoice.total_amount, invoice.period_month),
            need_email=True,
            send_email_to_provider=True,
        )
        await callback.answer()
    except Exception as exc:
        logger.error("Ошибка отправки инвойса %s: %s", payment_id, exc)
        await callback.answer("Не удалось создать платёж. Попробуйте позже.", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(
    query: PreCheckoutQuery, payment_repo: PaymentRepository,
) -> None:
    """Telegram ждёт ответа не дольше 10 секунд — полагаемся на кеш репозитория."""
    payment_id = query.invoice_payload
    invoice = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id),
        None,
    )
    if invoice is None:
        await query.answer(ok=False, error_message="Счёт не найден, попробуйте позже.")
        return
    if invoice.status == PaymentStatus.PAID:
        await query.answer(ok=False, error_message="Этот счёт уже оплачен.")
        return
    if query.total_amount != invoice.total_amount * 100:
        await query.answer(
            ok=False,
            error_message="Сумма счёта изменилась. Откройте «Мои счета» заново.",
        )
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    user: User | None,
    payment_service: PaymentService,
    user_repo: UserRepository,
) -> None:
    sp = message.successful_payment
    payment_id = sp.invoice_payload
    charge_id = sp.provider_payment_charge_id or ""
    comment = f"YK:{charge_id}" if charge_id else "YK"

    try:
        ok = await payment_service.confirm_payment(
            payment_id, message.from_user.id, comment=comment,
        )
    except Exception as exc:
        logger.error("Ошибка подтверждения оплаты %s (ЮKassa): %s", payment_id, exc)
        await message.answer(
            "✅ Платёж получен, но возникла ошибка записи. "
            "Администратор уже уведомлён."
        )
        ok = False

    if ok:
        await message.answer("✅ Оплата проведена. Спасибо!")
    else:
        logger.warning("Повторная / неуспешная запись оплаты %s", payment_id)

    # Уведомляем админов — даже если запись не удалась, чтобы разобрались вручную.
    try:
        admins = [u for u in await user_repo.get_all() if u.is_admin]
        tg_user = message.from_user
        username = f"@{tg_user.username}" if tg_user.username else "—"
        notify = (
            f"💰 Оплата через ЮKassa\n"
            f"Счёт: <code>{payment_id}</code>\n"
            f"Сумма: {sp.total_amount // 100} руб.\n"
            f"Плательщик: {username} (<code>{tg_user.id}</code>)\n"
            f"Статус записи: {'ок' if ok else '⚠️ требуется ручная проверка'}"
        )
        for admin in admins:
            try:
                await message.bot.send_message(admin.tg_id, notify)
            except Exception:
                pass
    except Exception:
        pass
