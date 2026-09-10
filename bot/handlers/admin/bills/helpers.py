from __future__ import annotations
import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from bot.repositories import (
    GroupRepository, StudentGroupRepository,
    ClientRepository,
)
from bot.services import PaymentService
from bot.services.parent_notifier import addrs_of, resolve_notifier, fmt_addr
from bot.utils.bill_format import build_bill_text
from bot.utils.dates import display_period, format_date_short_with_wd, last_periods
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
    recipients = addrs_of(student, client)  # Telegram и MAX
    notifier = resolve_notifier(callback.bot)

    sent_to = 0
    sent_invoices = 0
    for addr in recipients:
        if not await notifier.send(addr, bill_text):
            logger.error("Ошибка отправки родителю %s", fmt_addr(addr))
            continue
        sent_to += 1
        tg_id = addr[1]
        if settings.payment_provider_token and addr[0] == "tg":
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


def _bill_detail_lines(student_name: str, period_month: str, bills: dict, payments: list) -> list[str]:
    """Текст экрана «Счёт ученика за период»: разбивка по педагогам и занятиям.

    Общий для админского флоу и педагога с правом счетов (BILLING_TEACHER_IDS).
    """
    pay_by_teacher = {p.teacher_id: p for p in payments}
    lines = [f"<b>Счёт: {student_name}</b>", f"Период: {display_period(period_month)}", ""]
    grand_total = 0
    for teacher_id, agg in bills.items():
        subtotal = agg["total"]
        grand_total += subtotal
        p = pay_by_teacher.get(teacher_id)
        if p and p.status.value == "paid":
            status = f"✅ Оплачен ({p.paid_at or ''})"
        elif p:
            status = "📋 Ожидает оплаты"
        else:
            status = "⏳ Счёт не создан"
        lines.append(f"👨‍🏫 <b>{agg['name']}</b> — {subtotal} руб. — {status}")
        items = agg["items"]
        individual = [b for b in items if b.lesson_type != "group"]
        group_items = [b for b in items if b.lesson_type == "group"]
        if individual:
            lines.append("  <i>Индивидуальные:</i>")
            cur_date: str | None = None
            for b in sorted(individual, key=lambda x: x.date):
                if b.date != cur_date:
                    cur_date = b.date
                    lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
                lines.append(f"    · {b.duration_min} мин · {b.amount} руб.")
        if group_items:
            group_total = sum(b.amount for b in group_items)
            lines.append(f"  <i>Групповые ({len(group_items)} посещений, {group_total} руб.):</i>")
            cur_date = None
            for b in sorted(group_items, key=lambda x: x.date):
                if b.date != cur_date:
                    cur_date = b.date
                    lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
                lines.append(f"    · {b.duration_min} мин · {b.amount} руб.")
        lines.append("")
    lines.append(f"Итого: {grand_total} руб.")
    return lines


def _periods_only_buttons(action_prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    periods = last_periods(6)
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

