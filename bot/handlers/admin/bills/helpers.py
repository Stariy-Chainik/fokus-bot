from __future__ import annotations
import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from bot.repositories import (
    GroupRepository, StudentGroupRepository,
    ClientRepository,
)
from bot.services import PaymentService
from bot.services.payment_ledger import lesson_paid_marks
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
    ledgers = await payment_service.ledger_for(student, period)
    invoices = [r for ledger in ledgers.values() for r in ([*ledger.paid_rows] + ([ledger.pending] if ledger.pending else []))]
    paid_total = sum(ledger.paid for ledger in ledgers.values())
    bill_text, _ = build_bill_text(student.name, group_names, period, bills, paid=paid_total)

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
                if p.status.value != "paid" and p.total_amount > 0:
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
    groups_by_id = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
    names: list[str] = []
    for gid in await student_group_repo.get_groups_for_student(student_id):
        g = groups_by_id.get(gid)
        if g:
            names.append(g.name)
    return names


def _bill_detail_lines(student_name: str, period_month: str, bills: dict, payments: list) -> list[str]:
    """Текст экрана «Счёт ученика за период»: разбивка по педагогам и занятиям.

    Общий для админского флоу и педагога с правом счетов (BILLING_TEACHER_IDS).
    """
    paid_by_teacher: dict[str, int] = {}
    last_paid_at: dict[str, str] = {}
    has_pending: set[str] = set()
    for p in payments:
        if p.status.value == "paid":
            paid_by_teacher[p.teacher_id] = paid_by_teacher.get(p.teacher_id, 0) + p.total_amount
            last_paid_at[p.teacher_id] = max(last_paid_at.get(p.teacher_id, ""), (p.paid_at or "")[:10])
        else:
            has_pending.add(p.teacher_id)
    lines = [f"<b>Счёт: {student_name}</b>", f"Период: {display_period(period_month)}", ""]
    grand_total = 0
    grand_paid = 0
    for teacher_id, agg in bills.items():
        subtotal = agg.total
        grand_total += subtotal
        paid = paid_by_teacher.get(teacher_id, 0)
        grand_paid += min(paid, subtotal)
        if paid >= subtotal:
            status = f"✅ Оплачен ({last_paid_at.get(teacher_id, '')})"
        elif teacher_id in has_pending:
            status = f"⬜ Не оплачен — к оплате {subtotal - paid} руб."
        elif paid:
            status = f"⬜ Не оплачен — к оплате {subtotal - paid} руб."
        else:
            status = f"⏳ Счёт не создан — {subtotal} руб."
        lines.append(f"{'👥' if agg.group else '👨‍🏫'} <b>{agg.name}</b> — {subtotal} руб. — {status}")
        items = sorted(agg.items, key=lambda b: (b.date, b.lesson_id))
        marks = lesson_paid_marks([b.amount for b in items], paid)
        paid_items = [b for b, is_paid in zip(items, marks, strict=False) if is_paid]
        unpaid_items = [b for b, is_paid in zip(items, marks, strict=False) if not is_paid]

        def _append_items(title: str, mark: str, selected: list) -> None:
            if not selected:
                return
            lines.append(f"  <b>{mark} {title}:</b>")
            for b in selected:
                kind = "групп." if b.lesson_type == "group" else "инд."
                lines.append(
                    f"    {mark} {format_date_short_with_wd(b.date)} · {kind}"
                    f" · {b.duration_min} мин · {b.amount} ₽"
                )

        # Неоплаченные уроки всегда сверху, оплаченные — ниже.
        _append_items("К оплате", "⬜", unpaid_items)
        _append_items("Оплачено", "✅", paid_items)
        if agg.subscription and not items:
            mark = "✅" if paid >= subtotal else "⬜"
            lines.append(f"  {mark} Фиксированная сумма за месяц")
        lines.append("")
    grand_due = grand_total - grand_paid
    if grand_due:
        lines.append(f"Итого: {grand_total} руб. · оплачено {grand_paid} · к оплате {grand_due}")
    else:
        lines.append(f"Итого: {grand_total} руб. · ✅ оплачено")
    return lines


def _periods_only_buttons(action_prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    periods = last_periods(6)
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
