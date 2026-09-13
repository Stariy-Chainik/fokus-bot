"""Данные экранов родителя (счета, оплата) — без привязки к мессенджеру.

Функции возвращают DTO/списки; текст и кнопки собирают билдеры в bot/screens,
а хендлеры Telegram (aiogram) и MAX (maxapi) только доставляют результат.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO

from bot.utils.dates import display_period, last_periods, month_name_ru
from bot.services.parent_notifier import fmt_addr
from bot.services.payment_ledger import ledger_totals, lesson_paid_marks
from bot.services.payment_methods import callback_code

logger = logging.getLogger(__name__)


def period_label(period_month: str) -> str:
    year, month = period_month.split("-")
    return f"{month_name_ru(int(month))} {year}"


METHOD_LABELS = {
    "cash": "💵 Наличные",
    "bank": "🏦 По реквизитам",
    "sbp": "📱 СБП",
}


# ─── Список месяцев ──────────────────────────────────────────────────────────

@dataclass
class PeriodRow:
    period: str
    label: str
    icon: str


async def bills_periods(students: list, payment_service, show_older: bool = False) -> list[PeriodRow]:
    """show_older=False — текущий и прошлый месяц; True — остальные из последних 6.
    Иконка: ✅ всё оплачено, ⏳ есть остаток к оплате, 📅 текущий месяц."""
    all_periods = last_periods(6)
    periods = all_periods[2:] if show_older else all_periods[:2]
    rows: list[PeriodRow] = []
    for period_month in periods:
        accrued = paid = remainder = 0
        for student in students:
            ledgers = await payment_service.ledger_for(student, period_month)
            a, p_, r = ledger_totals(ledgers)
            accrued += a
            paid += p_
            remainder += r
        label = period_label(period_month)
        if period_month == periods[0]:
            icon = "📅"
            if not accrued:
                suffix = " — нет занятий"
            elif remainder and paid:
                suffix = f" — {accrued} руб., к доплате {remainder} (текущий)"
            else:
                suffix = f" — {accrued} руб. (текущий)"
        else:
            if accrued == 0:
                continue
            icon = "✅" if remainder == 0 else "⏳"
            suffix = f" — {accrued} руб." + (f", к доплате {remainder}" if remainder and paid else "")
        rows.append(PeriodRow(period_month, f"{label}{suffix}", icon))
    return rows


# ─── Детализация счёта ───────────────────────────────────────────────────────

@dataclass
class BillDetail:
    lines: list = field(default_factory=list)
    grand_total: int = 0
    paid_total: int = 0
    unpaid_total: int = 0
    overpaid_total: int = 0
    payment_ids: list = field(default_factory=list)

    @property
    def can_pay(self) -> bool:
        return self.unpaid_total > 0


async def bill_detail(students: list, period_month: str, payment_service) -> BillDetail:
    from bot.utils.dates import format_date_display
    d = BillDetail()
    title_who = f" — {students[0].name}" if len(students) == 1 else " — все дети"
    d.lines.append(f"<b>📋 {period_label(period_month)}{title_who}</b>\n")
    for student in students:
        ledgers = await payment_service.ledger_for(student, period_month)
        if not ledgers:
            continue
        if len(students) > 1:
            d.lines.append(f"<b>{student.name}:</b>")
        for _teacher_id, ledger in ledgers.items():
            if ledger.pending is not None:
                d.payment_ids.append(ledger.pending.payment_id)
            if ledger.subscription:
                status_mark = "✅" if ledger.fully_paid else "⬜"
                d.lines.append(f"<b>💳 {ledger.name} {status_mark}</b>")
                d.lines.append("  фиксированная сумма за месяц")
            else:
                d.lines.append(f"<b>Педагог: {ledger.name}</b>")
                items = sorted(ledger.items, key=lambda b: (b.date, b.lesson_id))
                marks = lesson_paid_marks([item.amount for item in items], ledger.paid)
                paid_items = [item for item, paid in zip(items, marks, strict=False) if paid]
                unpaid_items = [item for item, paid in zip(items, marks, strict=False) if not paid]

                # Неоплаченные занятия сразу видны сверху; оплаченные собраны ниже.
                # Статус бинарный: без отдельного статуса «частично оплачено».
                if unpaid_items:
                    d.lines.append("  <b>⬜ К оплате:</b>")
                    for item in unpaid_items:
                        d.lines.append(
                            f"    ⬜ {format_date_display(item.date)}  {item.duration_min} мин"
                            f"  — {item.amount} руб."
                        )
                if paid_items:
                    d.lines.append("  <b>✅ Оплачено:</b>")
                    for item in paid_items:
                        d.lines.append(
                            f"    ✅ {format_date_display(item.date)}  {item.duration_min} мин"
                            f"  — {item.amount} руб."
                        )
            if ledger.fully_paid:
                d.lines.append(f"  <i>Итого: {ledger.accrued} руб. — оплачено</i>\n")
            elif ledger.paid:
                d.lines.append(f"  <i>Итого: {ledger.accrued} руб. — оплачено {ledger.paid}, к доплате {ledger.remainder}</i>\n")
            else:
                d.lines.append(f"  <i>Итого: {ledger.accrued} руб.</i>\n")
            if ledger.overpaid:
                d.lines.append(f"  <i>переплата {ledger.overpaid} руб. — учтём в следующем месяце</i>\n")
            d.grand_total += ledger.accrued
            d.paid_total += ledger.paid
            d.unpaid_total += ledger.remainder
            d.overpaid_total += ledger.overpaid
    if d.grand_total == 0:
        d.lines = [f"📋 {period_label(period_month)}\n\nЗанятий не найдено."]
    elif d.unpaid_total > 0:
        if d.paid_total:
            d.lines.append(f"Оплачено: {d.paid_total} руб.")
        d.lines.append(f"<b>К оплате: {d.unpaid_total} руб.</b>")
    else:
        d.lines.append("✅ Период полностью оплачен")
    return d


# ─── Оплата ──────────────────────────────────────────────────────────────────

async def unpaid_for(student, period_month: str, payment_service) -> tuple[int, list]:
    """(сумма к оплате, [{tid, name, amount, pid}]) — остатки по педагогам (начислено − оплачено).
    pid — числовая часть PAY-id строки-остатка (для коротких callback)."""
    ledgers = await payment_service.ledger_for(student, period_month)
    unpaid = sorted(
        [{"tid": tid, "name": ledger.name, "amount": ledger.remainder, "pid": ledger.pending_pid}
         for tid, ledger in ledgers.items() if ledger.remainder > 0],
        key=lambda x: x["name"],
    )
    return sum(u["amount"] for u in unpaid), unpaid


def selected_from(data: dict, student_id: str, period_month: str, unpaid: list) -> list:
    """Выбранные педагоги из FSM-данных (валидные), по умолчанию — все неоплаченные."""
    if data.get("pay_sel_key") == f"{student_id}:{period_month}":
        chosen = set(data.get("pay_sel") or [])
        sel = [u for u in unpaid if u["tid"] in chosen]
        if sel:
            return sel
    return unpaid


def selection_fsm_data(sel: list, unpaid: list) -> dict:
    """Что положить в FSM для флоу «чек / наличные» после выбора способа оплаты."""
    return {
        "receipt_sel_pids": ".".join(str(u["pid"]) for u in sel if u["pid"]),
        "receipt_sel_label": ", ".join(u["name"] for u in sel),
        "receipt_sel_total": sum(u["amount"] for u in sel),
        "receipt_sel_partial": len(sel) < len(unpaid),
        "receipt_sel_tids": [u["tid"] for u in sel],
    }


async def client_contact(student, client_repo) -> tuple[str, str]:
    """(телефон, email) клиента для фискального чека ЮКассы."""
    if not student.client_id:
        return "", ""
    client = await client_repo.get_by_id(student.client_id)
    if not client:
        return "", ""
    return client.phone or "", client.email or ""


def qr_png(student_name: str, period_month: str, total: int) -> bytes | None:
    """QR по ГОСТ Р 56042 с назначением и суммой; None — реквизиты QR не заданы или ошибка."""
    from config.settings import settings
    if not settings.payment_qr_data:
        return None
    try:
        import qrcode
        purpose = f"Оплата занятий, {student_name}, {display_period(period_month)}"
        base = settings.payment_qr_data.replace("|INN=", "|PayeeINN=")
        img = qrcode.make(f"{base}|Purpose={purpose}|Sum={total * 100}")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Не удалось сгенерировать QR: %s", exc)
        return None


# ─── Сообщение админу о чеке / наличных ─────────────────────────────────────

def breakdown_lines(bills: dict, tids: list, limit: int = 850, ledgers: dict | None = None) -> list[str]:
    """Разбивка для админа: педагог/абонемент — сумма и даты занятий.
    ledgers (teacher_id → TeacherLedger) — показать «оплачено / к доплате» при частичной оплате.
    Если не влезает в подпись Telegram — короткий вариант (число занятий)."""
    full, short = [], []
    for tid in tids:
        agg = bills.get(tid)
        if not agg:
            continue
        ledger = (ledgers or {}).get(tid)
        paid_part = f" (оплачено {ledger.paid}, к доплате {ledger.remainder})" if ledger is not None and ledger.paid else ""
        items = sorted(agg.get("items") or [], key=lambda b: b.date)
        if agg.get("subscription") or not items:
            full.append(f"• {agg['name']} — {agg['total']} руб.{paid_part}")
            short.append(full[-1])
            continue
        dates = ", ".join(
            f"{b.date[8:10]}.{b.date[5:7]} ({b.duration_min}м{', группа' if b.lesson_type == 'group' else ''})"
            for b in items
        )
        full.append(f"• {agg['name']} — {agg['total']} руб.{paid_part}: {dates}")
        short.append(f"• {agg['name']} — {agg['total']} руб.{paid_part} ({len(items)} зан.)")
    return full if len("\n".join(full)) <= limit else short


def admin_confirm_rows(
    student_id: str, period_month: str, sel_pids: str, partial: bool, parent_addr, total: int | None = None,
    method: str = "admin_manual",
) -> list:
    """Кнопки админу: подтвердить (частично — только выбранные счета) / не подтверждать.
    total — сумма из чека/уведомления: зачитывается именно она (record_payment), а не остаток
    на момент нажатия (он мог вырасти после новых занятий).
    method кодируется одним символом в callback, чтобы точный способ дошёл до подтверждения."""
    from bot.screens import cb
    amount_part = f":{int(total)}" if total else ""
    method_part = f":{callback_code(method)}"
    confirm_cb = (
        f"rcpp:{student_id}:{period_month}:{sel_pids}{amount_part}{method_part}"
        if partial and sel_pids else f"receipt_confirm:{student_id}:{period_month}{amount_part}{method_part}"
    )
    return [
        [cb("✅ Подтвердить оплату", confirm_cb)],
        [cb("❌ Не подтверждать", f"rcpt_no:{student_id}:{period_month}:{fmt_addr(parent_addr)}")],
    ]


def receipt_caption(method: str, student_name: str, period_month: str, total: int, breakdown: str) -> str:
    return (
        f"📎 Чек об оплате\n\n"
        f"Способ: {METHOD_LABELS.get(method, method)}\n"
        f"Ученик: {student_name}\n"
        f"Период: {period_label(period_month)}\n"
        f"Сумма: {total} руб."
        + (f"\n\n{breakdown}" if breakdown else "")
    )


def cash_notice(student_name: str, period_month: str, total: int, breakdown: str) -> str:
    return (
        f"💵 Клиент сообщает об оплате наличными\n\n"
        f"Ученик: {student_name}\n"
        f"Период: {period_label(period_month)}\n"
        f"Сумма: {total} руб."
        + (f"\n\n{breakdown}" if breakdown else "")
    )
