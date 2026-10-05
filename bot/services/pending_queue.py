"""Очередь решений администратора: запись и закрытие строк `pending_actions`.

Тонкая обёртка над `PendingActionRepository`, чтобы хендлеры бота и API кабинета
не зависели от формы репозитория и чтобы очередь никогда не роняла основной
сценарий: если лист недоступен, оплата и уведомления всё равно проходят.
"""
from __future__ import annotations

import logging

from bot.repositories.pending_action_repo import (
    DONE, KIND_CASH, KIND_CHILD, KIND_RECEIPT, OPEN, REJECTED,
)
from bot.models.enums import PaymentStatus
from bot.services import activity, payment_events

logger = logging.getLogger(__name__)

__all__ = ["KIND_CASH", "KIND_CHILD", "KIND_RECEIPT", "OPEN", "DONE", "REJECTED",
           "queue_action", "open_receipt", "close_actions", "claim_action", "settle_actions", "rest_for_keys"]


async def queue_action(
    pending_repo, kind: str, student=None, period_month: str = "", *,
    amount: int = 0, method: str = "", parent_addr: str = "",
    student_id: str = "", student_name: str = "", file_id: str = "", file_type: str = "",
    comment: str = "", teacher_keys: list | None = None, held_by: str = "",
):
    """Поставить решение в очередь. Ошибка листа не прерывает сценарий — только лог.

    teacher_keys — за какие начисления платят (родитель выбрал часть педагогов): при
    одобрении сумма зачитывается именно им, а не первому по алфавиту.
    """
    if pending_repo is None:
        return None
    sid = student.student_id if student is not None else student_id
    name = student.name if student is not None else student_name
    try:
        action = await pending_repo.add(
            kind, sid, name, period_month, amount, method, parent_addr,
            file_id, file_type, comment, "|".join(teacher_keys or []), held_by,
        )
        await activity.record(activity.QUEUE, f"Заявка от родителя ({kind}): {sid} · {period_month}"
                              + (f" · {amount} ₽ · {method}" if amount else ""), ref=action.action_id)
        payment_events.request_created(action)       # копия с кнопками педагогу, который решает сам
        return action
    except Exception as exc:                      # очередь — вспомогательная, платёж важнее
        logger.error("Очередь решений: не записали %s для %s: %s", kind, sid, exc)
        return None


ONLINE_RECEIPT_WINDOW_MIN = 180


async def recent_online_payment(payment_repo, student_id: str, minutes: int = ONLINE_RECEIPT_WINDOW_MIN):
    """Свежий платёж ЮКассы ученика (succeeded за последние `minutes`) или None.

    Родители после СБП онлайн присылают чек ЮКассы как подтверждение «по реквизитам» — бот создавал
    заявку на следующий месяц, администратор её подтверждал, и оплата зачитывалась дважды
    (Авалян, 05.10.2026). Онлайн-платёж зачтён автоматически, чек на него не нужен.
    """
    from datetime import datetime, timedelta
    if payment_repo is None:
        return None
    since = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [p for p in await payment_repo.get_all()
            if p.student_id == student_id and p.status == PaymentStatus.PAID
            and (p.payment_method or "").startswith("yookassa") and (p.paid_at or "") >= since]
    return max(rows, key=lambda p: p.paid_at or "") if rows else None


async def open_receipt(pending_repo, student_id: str, period_month: str):
    """Открытая заявка-чек родителя по ученику и месяцу или None.

    Родители присылают один чек по нескольку раз — каждая копия приходила админам отдельным
    сообщением с кнопкой «Подтвердить». Пока первый чек ждёт решения, новые не пересылаем.
    Наличные у педагога (`held_by`) сюда не входят — это другая очередь.
    """
    if pending_repo is None:
        return None
    try:
        return next((a for a in await pending_repo.get_open()
                     if a.kind == KIND_RECEIPT and a.student_id == student_id
                     and a.period_month == period_month and not a.held_by), None)
    except Exception as exc:                      # очередь вспомогательная: при сбое чек пересылается как раньше
        logger.error("Очередь решений: не проверили дубль чека %s %s: %s", student_id, period_month, exc)
        return None


async def close_actions(
    pending_repo, student_id: str, period_month: str, status: str = DONE,
    decided_by_tg_id: int = 0, kinds: tuple = (KIND_CASH, KIND_RECEIPT),
) -> int:
    """Закрыть открытые решения по оплате ученика за месяц (решили в чате или в кабинете)."""
    if pending_repo is None:
        return 0
    try:
        return await pending_repo.close_for_period(student_id, period_month, status,
                                                   decided_by_tg_id, kinds)
    except Exception as exc:
        logger.error("Очередь решений: не закрыли %s %s: %s", student_id, period_month, exc)
        return 0


async def claim_action(pending_repo, action_id: str, status: str = DONE, decided_by_tg_id: int = 0) -> bool:
    """Занять решение один раз (идемпотентность подтверждений). False — уже решено."""
    if pending_repo is None or not action_id:
        return True                               # очередь недоступна — работаем по-старому
    try:
        claimed = await pending_repo.claim(action_id, status, decided_by_tg_id)
        if claimed and status == REJECTED:
            await activity.record(activity.QUEUE, f"Заявка {action_id} отклонена", actor=decided_by_tg_id, ref=action_id)
        return claimed
    except Exception as exc:
        logger.error("Очередь решений: не заняли %s: %s", action_id, exc)
        return True                               # лист недоступен — не блокируем оплату


def rest_for_keys(ledgers: dict, keys: list) -> int:
    """Остаток к оплате по выбранным начислениям (пустой список — по всем)."""
    return sum(v.remainder for k, v in ledgers.items() if not keys or k in keys)


async def settle_actions(
    pending_repo, payment_service, student, period_month: str, credited: int, decided_by_tg_id: int = 0,
) -> int:
    """После ручной отметки оплаты закрыть заявки родителя, которые она покрыла.

    Закрываем открытые наличные/чеки ученика за месяц, если остатка больше нет или заявленная
    сумма совпала с зачтённой. Иначе заявка висит в «Ждут решения», у родителя — «ждёт
    подтверждения», а повторное одобрение даёт переплату.
    """
    if pending_repo is None or student is None or credited <= 0:
        return 0
    try:
        open_rows = [a for a in await pending_repo.get_open()
                     if a.student_id == student.student_id and a.period_month == period_month
                     and a.kind in (KIND_CASH, KIND_RECEIPT) and not a.held_by]   # наличные у педагога — отдельно
        if not open_rows:
            return 0
        ledgers = await payment_service.ledger_for(student, period_month)
        rest = rest_for_keys(ledgers, [])
        closed = 0
        for a in open_rows:
            if rest == 0 or a.amount == credited:
                if await pending_repo.claim(a.action_id, DONE, decided_by_tg_id):
                    closed += 1
                    logger.info("Очередь решений: %s закрыта ручной отметкой %d руб. (%s %s)",
                                a.action_id, credited, student.student_id, period_month)
                    await activity.record(activity.QUEUE, f"Заявка {a.action_id} закрыта ручной отметкой:"
                                          f" {student.student_id} · {period_month}", actor=decided_by_tg_id, ref=a.action_id)
        return closed
    except Exception as exc:
        logger.error("Очередь решений: не закрыли заявки %s %s: %s", student.student_id, period_month, exc)
        return 0


async def awaiting_periods(pending_repo) -> set:
    """(student_id, period) с открытой заявкой об оплате: родитель сообщил, что заплатил
    (наличные, чек) или деньги у педагога. Таким не шлём напоминание о долге."""
    if pending_repo is None:
        return set()
    try:
        return {(a.student_id, a.period_month) for a in await pending_repo.get_open()
                if a.kind in (KIND_CASH, KIND_RECEIPT)}
    except Exception as exc:
        logger.warning("Очередь решений: не прочитали открытые заявки: %s", exc)
        return set()
