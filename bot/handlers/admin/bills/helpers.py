from __future__ import annotations
import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from bot.repositories import (
    GroupRepository, StudentGroupRepository,
    ClientRepository,
)
from bot.services import PaymentService
from bot.utils.bill_format import build_bill_text
from bot.utils.dates import display_period, last_periods
from config.settings import settings


logger = logging.getLogger(__name__)


async def _send_bill_to_parents(
    callback: CallbackQuery, student, period: str, bills: dict,
    group_names: list[str],
    payment_service: PaymentService, client_repo: ClientRepository,
) -> tuple[int, int, int]:
    """Отправить счёт родителям ученика. Возвращает (recipients_total, sent_to, sent_invoices)."""
    invoices = await payment_service.get_or_create_invoices_for_student_period(student, period)
    bill_text, _ = build_bill_text(student.name, group_names, period, bills)

    client = await client_repo.get_by_id(student.client_id) if student.client_id else None
    recipients: list[int] = []
    if client and client.tg_id:
        recipients.append(client.tg_id)
    for pid in (student.parent_tg_ids or []):
        if pid not in recipients:
            recipients.append(pid)

    sent_to = 0
    sent_invoices = 0
    for tg_id in recipients:
        try:
            await callback.bot.send_message(tg_id, bill_text)
            sent_to += 1
        except Exception as exc:
            logger.error("Ошибка отправки родителю tg_id=%s: %s", tg_id, exc)
            continue
        if settings.payment_provider_token:
            for p in invoices:
                if p.status.value != "paid":
                    try:
                        await callback.bot.send_invoice(
                            chat_id=tg_id,
                            title=f"Занятия {display_period(period)}",
                            description=f"Педагог: {p.teacher_name or '—'}",
                            payload=p.payment_id,
                            provider_token=settings.payment_provider_token,
                            currency="RUB",
                            prices=[LabeledPrice(label="Обучение", amount=p.total_amount * 100)],
                        )
                        sent_invoices += 1
                    except Exception as exc:
                        logger.error("Ошибка инвойса %s: %s", p.payment_id, exc)
    return len(recipients), sent_to, sent_invoices


async def _student_group_names(
    student_id: str,
    student_group_repo: StudentGroupRepository, group_repo: GroupRepository,
) -> list[str]:
    """Названия всех групп ученика — для шапки счёта."""
    names: list[str] = []
    for gid in await student_group_repo.get_groups_for_student(student_id):
        g = await group_repo.get_by_id(gid)
        if g:
            names.append(g.name)
    return names


def _periods_only_buttons(action_prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    periods = last_periods(6)
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

