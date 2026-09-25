"""Данные экранов родителя (счета, оплата) — без привязки к мессенджеру.

Функции возвращают DTO/списки; текст и кнопки собирают билдеры в bot/screens,
а хендлеры Telegram (aiogram) и MAX (maxapi) только доставляют результат.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from bot.utils.dates import display_period, last_periods, period_label
from bot.services.parent_notifier import fmt_addr
from bot.screens.parent_bills import BillDetail, render_bill_detail  # noqa: F401 — BillDetail реэкспорт
from bot.services.payment_ledger import ledger_totals
from bot.services.payment_methods import callback_code
from config.settings import settings

logger = logging.getLogger(__name__)


def history_hidden(period: str) -> bool:
    """Месяц раньше PARENT_BILLS_SINCE_PERIOD родителю не показывается нигде.

    Кабинет для родителей заработал с сентября 2026; всё, что раньше, — архив
    школы (апрель–август закрыты оптом 09.09.2026). Одна граница на кабинет,
    Telegram-бот и MAX: счета, занятия, дневник.
    """
    since = settings.parent_bills_since_period
    return bool(since and period[:7] < since)


def visible_periods(count: int) -> list:
    """Последние `count` месяцев, которые родителю можно показывать."""
    return [ym for ym in last_periods(count) if not history_hidden(ym)]


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
    all_periods = visible_periods(6)
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

async def bill_detail(students: list, period_month: str, payment_service) -> BillDetail:
    """Счета по педагогам за месяц (с синхронизацией остатков) → экран из bot/screens.

    Отдельно добавляем занятия педагогов с прямой оплатой: родитель видит их
    суммы так же, как в кабинете, но платит за них педагогу лично.
    """
    ledgers_by_student = [(student, await payment_service.ledger_for(student, period_month)) for student in students]
    direct_by_student = {
        student.student_id: await payment_service.direct_pay_rows(student.student_id, period_month)
        for student in students
    }
    return render_bill_detail(period_month, ledgers_by_student, direct_by_student)


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
    """Выбранные педагоги из FSM-данных (валидные), по умолчанию — все неоплаченные.

    Если родитель выбрал отдельные занятия (`pay_amounts`), сумма педагога — за них.
    """
    amounts = data.get("pay_amounts") or {}
    if data.get("pay_sel_key") == f"{student_id}:{period_month}":
        chosen = set(data.get("pay_sel") or [])
        sel = [{**u, "amount": amounts.get(u["tid"], u["amount"])} for u in unpaid if u["tid"] in chosen]
        if sel:
            return sel
    return [{**u, "amount": amounts.get(u["tid"], u["amount"])} for u in unpaid]


def selection_fsm_data(sel: list, unpaid: list) -> dict:
    """Что положить в FSM для флоу «чек / наличные» после выбора способа оплаты."""
    return {
        "receipt_sel_pids": ".".join(str(u["pid"]) for u in sel if u["pid"]),
        "receipt_sel_label": ", ".join(u["name"] for u in sel),
        "receipt_sel_total": sum(u["amount"] for u in sel),
        "receipt_sel_partial": len(sel) < len(unpaid),
        "receipt_sel_tids": [u["tid"] for u in sel],
    }


async def cash_options(student_id: str, student_group_repo) -> tuple[bool, bool]:
    """(наличные разрешены, наличные предпочтительны) для ученика.

    Школа принимает наличные не везде: `CASH_DISABLED_GROUP_IDS` убирает способ
    совсем, `CASH_PREFERRED_GROUP_IDS` ставит его первым (спортивные группы).
    """
    from config.settings import settings
    gids = set(await student_group_repo.get_groups_for_student(student_id))
    allowed = settings.payment_cash_enabled and not (gids & settings.cash_disabled_group_id_set)
    return allowed, bool(allowed and gids & settings.cash_preferred_group_id_set)


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
    """Разбивка для админа: педагог/абонемент — сумма и даты занятий (bills: {ключ → BillAggregate}).
    ledgers (teacher_id → TeacherLedger) — показать «оплачено / к доплате» при частичной оплате.
    Если не влезает в подпись Telegram — короткий вариант (число занятий)."""
    full, short = [], []
    for tid in tids:
        agg = bills.get(tid)
        if not agg:
            continue
        ledger = (ledgers or {}).get(tid)
        paid_part = f" (оплачено {ledger.paid}, к доплате {ledger.remainder})" if ledger is not None and ledger.paid else ""
        items = sorted(agg.items, key=lambda b: b.date)
        if agg.subscription or not items:
            full.append(f"• {agg.name} — {agg.total} руб.{paid_part}")
            short.append(full[-1])
            continue
        dates = ", ".join(
            f"{b.date[8:10]}.{b.date[5:7]} ({b.duration_min}м{', группа' if b.lesson_type == 'group' else ''})"
            for b in items
        )
        full.append(f"• {agg.name} — {agg.total} руб.{paid_part}: {dates}")
        short.append(f"• {agg.name} — {agg.total} руб.{paid_part} ({len(items)} зан.)")
    return full if len("\n".join(full)) <= limit else short


def admin_confirm_rows(
    student_id: str, period_month: str, sel_pids: str, partial: bool, parent_addr, total: int | None = None,
    method: str = "admin_manual", action_id: str = "",
) -> list:
    """Кнопки админу: подтвердить (частично — только выбранные счета) / не подтверждать.
    total — сумма из чека/уведомления: зачитывается именно она (record_payment), а не остаток
    на момент нажатия (он мог вырасти после новых занятий).
    method кодируется одним символом в callback, чтобы точный способ дошёл до подтверждения.
    action_id — строка очереди решений: подтверждение по ней идемпотентно, сколько бы копий
    уведомления ни висело в чатах (`pact:` вместо прежних `receipt_confirm:`/`rcpp:`)."""
    from bot.screens import cb
    amount_part = f":{int(total)}" if total else ""
    method_part = f":{callback_code(method)}"
    if action_id:
        return [
            [cb("✅ Подтвердить оплату", f"pact:{action_id}{amount_part}{method_part}")],
            [cb("❌ Не подтверждать", f"pnay:{action_id}")],
        ]
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
