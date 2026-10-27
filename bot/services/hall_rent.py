"""Аренда зала помесячно (решение владельца 27.10.2026).

Педагог с прямой оплатой (DIRECT_PAY_TEACHER_IDS, на проде Клецова) получает деньги за индивидуальные
занятия от родителей сам, а школе перечисляет аренду зала — одной суммой в конце месяца. Зарплату за группы
школа платит ему как обычно: две цифры рядом, без взаимозачёта.

Сумма месяца — `profit_service.lesson_rent` по занятиям: ставка педагога (HALL_RENT_PER_LESSON) или ставка
за ученика (HALL_RENT_STUDENT_RATES: Мадатова, Филатова — 800 вместо 500) за каждый начатый час.
С месяца HALL_RENT_SETTLE_SINCE_PERIOD (на проде 2026-11) аренда сводится: у педагога в «Зарплате» —
статус «получена / к оплате», на сводке — напоминание за прошлый месяц; у администратора в «Зарплатах» и
карточке педагога — «✅ Аренда получена» (лист hall_rent_payments, scripts/setup_hall_rent.py). Раньше этого
месяца суммы справочные, как и были. В «Прибыли» ничего не меняется — аренда там считается по занятиям.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.repositories.hall_rent_repo import HallRentPayment
from bot.services import activity
from bot.services.profit_service import lesson_rent, rate_active
from bot.utils.dates import period_label
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class RentStudent:
    student_id: str
    lessons: int = 0
    amount: int = 0


def settled(period: str) -> bool:
    """С этого месяца аренда сводится помесячно; пусто — только справочные суммы."""
    since = settings.hall_rent_settle_since_period
    return bool(since) and period >= since


def month_rent(lessons: list) -> int:
    return sum(lesson_rent(ls) for ls in lessons)


def rent_by_student(lessons: list) -> dict[str, RentStudent]:
    """Аренда занятия — ученику, чья ставка её определила (в паре — у кого личная ставка), иначе первому."""
    personal = settings.hall_rent_student_map
    out: dict[str, RentStudent] = {}
    for ls in lessons:
        rent = lesson_rent(ls)
        if not rent:
            continue
        ids = [s for s in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id) if s]
        if not ids:
            continue
        sid = next((s for s in ids if (ls.teacher_id, s) in personal and rate_active(personal[(ls.teacher_id, s)][1], ls.date)),
                   ids[0])
        item = out.setdefault(sid, RentStudent(sid))
        item.lessons += 1
        item.amount += rent
    return out


def _status(accrued: int, paid: int) -> str:
    if not accrued and not paid:
        return "none"
    if paid >= accrued:
        return "paid"
    return "partial" if paid else "due"


async def status(rent_repo, teacher_id: str, period: str, lessons: list) -> dict:
    """Аренда педагога за месяц: начислено по занятиям, получено по листу, статус; `settle` — месяц сводится."""
    accrued = month_rent(lessons)
    rows = await rent_repo.get_for_teacher_period(teacher_id, period) if rent_repo is not None else []
    paid = sum(r.amount for r in rows)
    return {
        "accrued": accrued, "paid": paid, "rest": max(accrued - paid, 0),
        "lessons": sum(1 for ls in lessons if lesson_rent(ls)),
        "status": _status(accrued, paid), "settle": settled(period),
        "payments": [{"id": r.rent_id, "amount": r.amount, "date": r.paid_at[:10], "comment": r.comment} for r in rows],
    }


async def record_payment(rent_repo, teacher_id: str, period: str, amount: int, by_tg_id: int,
                         comment: str = "") -> HallRentPayment:
    row = await rent_repo.add(teacher_id, period, amount, by_tg_id, comment)
    await activity.record(activity.FINANCE, f"Аренда зала получена {amount} ₽: {teacher_id} · {period}"
                          + (f" · {comment}" if comment else ""), actor=by_tg_id, ref=row.rent_id)
    return row


async def cancel_payment(rent_repo, rent_id: str, by_tg_id: int) -> HallRentPayment | None:
    row = await rent_repo.get_by_id(rent_id)
    if row is None:
        return None
    await rent_repo.delete(rent_id)
    await activity.record(activity.FINANCE, f"Отметка аренды снята {row.amount} ₽: {row.teacher_id} · {row.period_month}",
                          actor=by_tg_id, ref=rent_id)
    return row


def received_text(period: str, amount: int, rest: int) -> str:
    """Педагогу: школа получила аренду."""
    text = f"✅ Школа получила аренду зала за {period_label(period).lower()}: {amount} ₽. Спасибо!"
    return text + (f"\nОстаток за месяц: {rest} ₽." if rest else "")


async def notify_teacher(bot, user_repo, teacher_id: str, text: str) -> bool:
    """Сообщение педагогу в Telegram, если у него есть аккаунт в боте; ошибки доставки не мешают отметке."""
    if bot is None:
        return False
    user = next((u for u in await user_repo.get_all() if u.teacher_id == teacher_id), None)
    if user is None:
        return False
    try:
        await bot.send_message(user.tg_id, text)
        return True
    except Exception as exc:  # уведомление не должно ронять отметку
        logger.warning("Аренда: педагогу %s не доставлено: %s", teacher_id, exc)
        return False
