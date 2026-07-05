from __future__ import annotations
"""
Админ: экран «⚠️ Должники» — сводный контроль оплат клиентов.

Долг = неоплаченные начисления по всем периодам (см. PaymentService.compute_debt_map).
Текущий месяц показывается со звёздочкой («начислено на сегодня»), но в массовое
напоминание родителям НЕ входит — напоминаем только за закрытые месяцы
(правило месячного закрытия PER_VISIT-счетов).
"""
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

from bot.models import User
from bot.repositories import StudentRepository
from bot.services import PaymentService
from bot.utils.dates import display_period
from bot.utils.locks import InProgressGuard

logger = logging.getLogger(__name__)
router = Router(name="admin_debtors")

from bot.handlers.access import is_admin as _is_admin

_PAGE_SIZE = 25
_reminding = InProgressGuard()


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


async def _collect_debtors(
    payment_service: PaymentService, student_repo: StudentRepository,
) -> list[dict]:
    """Список должников, отсортированный по убыванию долга.

    Элемент: {student, periods: {period → ₽}, total, closed_total, has_parent}.
    closed_total — долг только за закрытые (прошедшие) месяцы.
    """
    debt_map = await payment_service.compute_debt_map()
    if not debt_map:
        return []
    students = {s.student_id: s for s in await student_repo.get_all()}
    current = _current_period()

    debtors: list[dict] = []
    for sid, periods in debt_map.items():
        student = students.get(sid)
        if student is None:
            logger.warning("Должник %s не найден в students — пропускаем", sid)
            continue
        total = sum(periods.values())
        closed_total = sum(amt for p, amt in periods.items() if p < current)
        debtors.append({
            "student": student,
            "periods": dict(sorted(periods.items())),
            "total": total,
            "closed_total": closed_total,
            "has_parent": bool(student.parent_tg_ids),
        })
    debtors.sort(key=lambda d: d["total"], reverse=True)
    return debtors


def _format_line(idx: int, d: dict, current: str) -> str:
    parts = []
    for period, amt in d["periods"].items():
        mark = "*" if period == current else ""
        parts.append(f"{display_period(period)}{mark}: {amt}")
    detail = "; ".join(parts)
    no_bot = "" if d["has_parent"] else " 🔕"
    return f"{idx}. <b>{d['student'].name}</b> — {d['total']} ₽{no_bot}\n   {detail}"


def _kb_debtors(page: int, pages: int, can_remind: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="«", callback_data=f"debtors:p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="»", callback_data=f"debtors:p:{page + 1}"))
        rows.append(nav)
    if can_remind:
        rows.append([InlineKeyboardButton(
            text="📤 Напомнить всем (закрытые месяцы)", callback_data="debtors:remind",
        )])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="debtors:p:0")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_debtors(
    callback: CallbackQuery, page: int,
    payment_service: PaymentService, student_repo: StudentRepository,
) -> None:
    debtors = await _collect_debtors(payment_service, student_repo)
    current = _current_period()

    if not debtors:
        await callback.message.edit_text(
            "⚠️ <b>Должники</b>\n\nДолгов нет — все начисления оплачены 🎉",
            reply_markup=_kb_debtors(0, 1, can_remind=False),
        )
        await callback.answer()
        return

    pages = (len(debtors) + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = max(0, min(page, pages - 1))
    chunk = debtors[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

    grand_total = sum(d["total"] for d in debtors)
    closed_grand = sum(d["closed_total"] for d in debtors)
    remind_targets = sum(1 for d in debtors if d["closed_total"] > 0 and d["has_parent"])

    lines = [
        "⚠️ <b>Должники</b>",
        "",
        f"Всего: {len(debtors)} · Долг: <b>{grand_total} ₽</b> "
        f"(закрытые месяцы: {closed_grand} ₽)",
        "",
    ]
    for i, d in enumerate(chunk, start=page * _PAGE_SIZE + 1):
        lines.append(_format_line(i, d, current))
    lines += [
        "",
        f"* — {display_period(current)}, начислено на сегодня (в напоминание не входит)",
        "🔕 — у родителя нет доступа к боту",
    ]

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_kb_debtors(page, pages, can_remind=remind_targets > 0),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:debtors")
async def cb_debtors(
    callback: CallbackQuery, user: User | None,
    payment_service: PaymentService, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _render_debtors(callback, 0, payment_service, student_repo)


@router.callback_query(F.data.startswith("debtors:p:"))
async def cb_debtors_page(
    callback: CallbackQuery, user: User | None,
    payment_service: PaymentService, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    page = int(callback.data.rsplit(":", 1)[1])
    await _render_debtors(callback, page, payment_service, student_repo)


@router.callback_query(F.data == "debtors:remind")
async def cb_debtors_remind_confirm(
    callback: CallbackQuery, user: User | None,
    payment_service: PaymentService, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    debtors = await _collect_debtors(payment_service, student_repo)
    targets = [d for d in debtors if d["closed_total"] > 0 and d["has_parent"]]
    skipped = sum(1 for d in debtors if d["closed_total"] > 0 and not d["has_parent"])
    if not targets:
        await callback.answer("Некому напоминать: нет должников с доступом к боту", show_alert=True)
        return
    total = sum(d["closed_total"] for d in targets)
    text = (
        "📤 <b>Напомнить всем должникам?</b>\n\n"
        f"Родителям будет отправлено напоминание о долге за <b>закрытые месяцы</b>.\n\n"
        f"Получателей: {len(targets)} · Сумма долгов: {total} ₽\n"
        + (f"Без доступа к боту (не получат): {skipped}\n" if skipped else "")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="debtors:remind_go")],
        [InlineKeyboardButton(text="« Отмена", callback_data="debtors:p:0")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "debtors:remind_go")
async def cb_debtors_remind_go(
    callback: CallbackQuery, user: User | None,
    payment_service: PaymentService, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lock_key = str(callback.from_user.id)
    if lock_key in _reminding:
        await callback.answer("Рассылка уже идёт, подождите.", show_alert=True)
        return
    _reminding.add(lock_key)
    try:
        debtors = await _collect_debtors(payment_service, student_repo)
        targets = [d for d in debtors if d["closed_total"] > 0 and d["has_parent"]]
        current = _current_period()

        sent_parents = 0
        failed = 0
        for d in targets:
            period_lines = [
                f"  • {display_period(p)}: {amt} ₽"
                for p, amt in d["periods"].items() if p < current
            ]
            text = (
                "🔔 <b>Напоминание об оплате</b>\n\n"
                f"Ученик: <b>{d['student'].name}</b>\n"
                f"Задолженность: <b>{d['closed_total']} ₽</b>\n"
                + "\n".join(period_lines)
                + "\n\nДетали и оплата — в разделе «💳 Мои счета»."
            )
            delivered = False
            for tg_id in d["student"].parent_tg_ids:
                try:
                    await callback.bot.send_message(tg_id, text)
                    delivered = True
                except TelegramAPIError as exc:
                    logger.warning("Напоминание не доставлено tg_id=%s (ученик %s): %s",
                                   tg_id, d["student"].student_id, exc)
            if delivered:
                sent_parents += 1
            else:
                failed += 1

        logger.info("Напоминания о долгах: доставлено %d, не доставлено %d", sent_parents, failed)
        summary = (
            "📤 <b>Напоминания отправлены</b>\n\n"
            f"Доставлено: {sent_parents} из {len(targets)}"
            + (f"\nНе доставлено (Telegram недоступен): {failed}" if failed else "")
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К должникам", callback_data="debtors:p:0")],
        ])
        await callback.message.edit_text(summary, reply_markup=kb)
        await callback.answer()
    finally:
        _reminding.discard(lock_key)
