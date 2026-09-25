"""Счета и оплата родителя — текст и ряды кнопок, общие для Telegram и MAX.

Все callback-строки те же, что в Telegram-боте (client_bill:, client_pay:, pay_method: …),
поэтому один и тот же экран обслуживают оба фронта.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.services.payment_ledger import lesson_paid_marks
from bot.utils.dates import format_date_display, period_label
from .types import cb, url

HOME = "go:home"


@dataclass
class BillDetail:
    lines: list = field(default_factory=list)
    grand_total: int = 0
    paid_total: int = 0
    unpaid_total: int = 0
    overpaid_total: int = 0
    direct_total: int = 0        # оплачивается педагогу напрямую, вне счёта школы
    payment_ids: list = field(default_factory=list)

    @property
    def can_pay(self) -> bool:
        return self.unpaid_total > 0


def render_bill_detail(
    period_month: str, ledgers_by_student: list, direct_by_student: dict | None = None,
) -> BillDetail:
    """ledgers_by_student — [(student, {teacher_id: TeacherLedger})]; чистая сборка текста счёта.

    direct_by_student — {student_id: [DirectPayRow]}: занятия педагогов с прямой оплатой.
    Показываем их отдельным блоком (как в кабинете), но в суммы счёта не берём —
    эти деньги родитель платит педагогу лично.
    """
    students = [student for student, _ in ledgers_by_student]
    direct_by_student = direct_by_student or {}
    d = BillDetail()
    title_who = f" — {students[0].name}" if len(students) == 1 else " — все дети"
    d.lines.append(f"<b>📋 {period_label(period_month)}{title_who}</b>\n")
    for student, ledgers in ledgers_by_student:
        if not ledgers and not direct_by_student.get(student.student_id):
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
                d.lines.append(f"<b>{'Группа' if ledger.group else 'Педагог'}: {ledger.name}</b>")
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
        for row in direct_by_student.get(student.student_id, []):
            d.lines.append(f"<b>Педагог: {row.name} — оплата напрямую</b>")
            for item in row.lessons:
                d.lines.append(
                    f"    {format_date_display(item.date)}  {item.duration_min} мин"
                    f"  — {item.amount} руб."
                )
            d.lines.append(f"  <i>Итого: {row.total} руб. — оплачивается педагогу напрямую</i>")
            d.lines.append("  <i>в «К оплате» не входит, школа эти занятия не отслеживает</i>\n")
            d.direct_total += row.total
    if d.grand_total == 0 and not d.direct_total:
        d.lines = [f"📋 {period_label(period_month)}\n\nЗанятий не найдено."]
    elif d.unpaid_total > 0:
        if d.paid_total:
            d.lines.append(f"Оплачено: {d.paid_total} руб.")
        d.lines.append(f"<b>К оплате: {d.unpaid_total} руб.</b>")
    else:
        d.lines.append("✅ Период полностью оплачен")
    return d


def bill_back_rows(student_id: str, period_month: str, home_cb: str = HOME) -> list:
    return [
        [cb("« К счёту", f"client_bill:{student_id}:{period_month}")],
        [cb("« Меню", home_cb)],
    ]


def student_select_screen(students: list, section: str) -> tuple:
    """section = 'lessons' | 'bills'."""
    prefix = "cl_stu" if section == "lessons" else "cl_bills_stu"
    rows = [[cb(s.name, f"{prefix}:{s.student_id}")] for s in students]
    rows.append([cb("👨‍👩‍👧 Все вместе", f"{prefix}:all")])
    rows.append([cb("« Меню", HOME)])
    return "Выберите ученика:", rows


def bills_list_screen(
    period_rows: list, student_id: str, who: str, show_older: bool, show_back: bool,
    has_older: bool = True,
) -> tuple:
    """period_rows: [PeriodRow]. Пустой список — сообщение «не найдено».
    has_older=False — раньше показывать нечего (история с сентября), кнопки «Другие месяцы» нет."""
    if not period_rows:
        text = ("📋 За более ранние месяцы занятий не найдено." if show_older
                else "📋 Занятий за текущий и прошлый месяц не найдено.")
    else:
        text = f"<b>💳 Оплата занятий — {who}</b>" + ("\nДругие месяцы:" if show_older else "")
    rows = [[cb(f"{r.icon} {r.label}", f"client_bill:{student_id}:{r.period}")] for r in period_rows]
    if show_older:
        rows.append([cb("« К текущим месяцам", f"cl_bills_stu:{student_id}")])
    else:
        if has_older:
            rows.append([cb("📆 Другие месяцы", f"cl_bills_more:{student_id}")])
        if student_id != "all" and show_back:
            rows.append([cb("« Назад", "client:my_bills")])
    rows.append([cb("« Меню", HOME)])
    return text, rows


def bill_detail_screen(detail, period_month: str, student_id: str) -> tuple:
    rows = []
    if detail.can_pay:
        rows.append([cb("💳 Оплатить", f"client_pay:{student_id}:{period_month}")])
    rows.append([cb("« Назад", f"cl_bills_stu:{student_id}")])
    rows.append([cb("« Меню", HOME)])
    return "\n".join(detail.lines), rows


def teacher_select_screen(student_id: str, period_month: str, who: str, unpaid: list, chosen: set) -> tuple:
    rows = []
    for i, u in enumerate(unpaid):
        mark = "✅" if u["tid"] in chosen else "⬜"
        rows.append([cb(f"{mark} {u['name']} — {u['amount']} руб.", f"pselt:{i}")])
    total = sum(u["amount"] for u in unpaid if u["tid"] in chosen)
    if total > 0:
        rows.append([cb(f"➡️ К оплате: {total} руб.", "pselgo")])
    rows.append([cb("« К счёту", f"client_bill:{student_id}:{period_month}")])
    who_part = f" — {who}" if who else ""
    text = (f"<b>💳 Оплата за {period_label(period_month)}{who_part}</b>\n"
            f"Отметьте, каких педагогов оплачиваете:")
    return text, rows


def lesson_select_screen(student_id: str, period_month: str, who: str, name: str,
                        marks: list, chosen: set) -> tuple:
    """Родитель выбирает занятия, за которые платит сейчас: ⬜/✅, оплаченные — без кнопки."""
    rows = []
    total = 0
    for i, m in enumerate(marks):
        if m["paid"]:
            rows.append([cb(f"✅ {format_date_display(m['date'])} — {m['amount']} руб. (оплачено)", "noop")])
            continue
        mark = "☑️" if m["lesson_id"] in chosen else "⬜"
        if m["lesson_id"] in chosen:
            total += m["amount"]
        rows.append([cb(f"{mark} {format_date_display(m['date'])} · {m['duration_min']} мин — {m['amount']} руб.",
                        f"plsn:{i}")])
    if total:
        rows.append([cb(f"➡️ Оплатить выбранное: {total} руб.", "plsngo")])
    rows.append([cb("Оплатить всё", "plsnall")])
    rows.append([cb("« К счёту", f"client_bill:{student_id}:{period_month}")])
    who_part = f" — {who}" if who else ""
    text = (f"<b>🧾 {name} · {period_label(period_month)}{who_part}</b>\n"
            f"Отметьте занятия, за которые платите сейчас. Можно оплатить всё сразу.")
    return text, rows


def methods_screen(student_id: str, period_month: str, who: str, sel: list, yookassa: bool,
                   cash: bool = False, bank: bool = True, sbp: bool = False) -> tuple:
    total = sum(u["amount"] for u in sel)
    who_part = f" — {who}" if who else ""
    lines = [f"<b>💳 Оплата за {period_label(period_month)}{who_part}</b>"]
    for u in sel:
        lines.append(f"  • {u['name']} — {u['amount']} руб.")
    lines.append(f"Сумма: <b>{total} руб.</b>\n\nВыберите способ оплаты:")
    lines.append(
        "\n<i>💵 Наличные — передайте администратору или педагогу, он подтвердит оплату.\n"
        "🏦 По реквизитам — после перевода обязательно прикрепите чек, иначе оплата не будет зачтена.\n"
        "📱 СБП онлайн — оплата подтверждается автоматически, чек прикреплять не нужно.</i>"
    )
    rows = []                       # порядок: наличные → по реквизитам с чеком → СБП онлайн
    if cash:
        rows.append([cb("💵 Наличные", f"pay_method:cash:{student_id}:{period_month}")])
    if bank:
        rows.append([cb("🏦 По реквизитам", f"pay_method:bank:{student_id}:{period_month}")])
    if sbp:
        rows.append([cb("📱 СБП", f"pay_method:sbp:{student_id}:{period_month}")])
    if yookassa:
        rows.append([cb("📱 СБП онлайн", f"pay_method:ysbp:{student_id}:{period_month}")])
    rows.append([cb("« К счёту", f"client_bill:{student_id}:{period_month}")])
    return "\n".join(lines), rows


def cash_screen(total: int, student_id: str, period_month: str) -> tuple:
    text = (f"<b>💵 Оплата наличными</b>\n"
            f"Сумма: <b>{total} руб.</b>\n\n"
            f"Передайте деньги администратору или преподавателю.\n"
            f"Нажмите кнопку, чтобы уведомить администратора.")
    rows = [
        [cb("📨 Уведомить об оплате", f"cash_notify:{student_id}:{period_month}")],
        [cb("« Назад", f"client_pay:{student_id}:{period_month}")],
    ]
    return text, rows


def receipt_rows(method: str, student_id: str, period_month: str) -> list:
    return [
        [cb("📎 Прикрепить чек", f"receipt_upload:{method}:{student_id}:{period_month}")],
        [cb("« Назад", f"client_pay:{student_id}:{period_month}")],
    ]


def bank_screen(total: int, student_id: str, period_month: str, bank_details: str) -> tuple:
    lines = ["<b>🏦 Оплата по реквизитам</b>", f"Сумма: <b>{total} руб.</b>", ""]
    if bank_details:
        lines += [bank_details.replace("\\n", "\n"), ""]
    lines.append("После оплаты прикрепите фото чека.")
    return "\n".join(lines), receipt_rows("bank", student_id, period_month)


def sbp_screen(total: int, student_id: str, period_month: str, sbp_details: str) -> tuple:
    lines = ["<b>📱 Оплата через СБП</b>", f"Сумма: <b>{total} руб.</b>", ""]
    if sbp_details:
        lines += [sbp_details, ""]
    lines.append("После оплаты прикрепите фото чека.")
    return "\n".join(lines), receipt_rows("sbp", student_id, period_month)


def online_pay_screen(kind: str, total: int, pay_url: str, student_id: str, period_month: str) -> tuple:
    if kind == "ysbp":
        text = (f"<b>📱 Оплата через СБП</b>\n"
                f"Сумма: <b>{total} руб.</b>\n\n"
                f"Нажмите кнопку — откроется страница СБП (QR или переход в банк).\n"
                f"После оплаты статус обновится автоматически.")
        label = "📱 Перейти к оплате"
    else:
        text = (f"<b>💳 Оплата картой онлайн</b>\n"
                f"Сумма: <b>{total} руб.</b>\n\n"
                f"Нажмите кнопку для перехода на страницу оплаты.\n"
                f"После оплаты статус обновится автоматически.")
        label = "💳 Перейти к оплате"
    rows = [[url(label, pay_url)], [cb("« К счёту", f"client_bill:{student_id}:{period_month}")]]
    return text, rows


def receipt_prompt_screen(student_id: str, period_month: str) -> tuple:
    return "📎 Отправьте фото или документ чека об оплате:", [
        [cb("« Отмена", f"client_pay:{student_id}:{period_month}")],
    ]


def receipt_sent_screen(student_id: str, period_month: str) -> tuple:
    return "✅ Чек отправлен администратору. Ожидайте подтверждения.", bill_back_rows(student_id, period_month)


def cash_sent_screen(student_id: str, period_month: str) -> tuple:
    return "✅ Администратор уведомлён. Ожидайте подтверждения.", bill_back_rows(student_id, period_month)
