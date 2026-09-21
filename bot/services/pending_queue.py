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

logger = logging.getLogger(__name__)

__all__ = ["KIND_CASH", "KIND_CHILD", "KIND_RECEIPT", "OPEN", "DONE", "REJECTED",
           "queue_action", "close_actions", "claim_action"]


async def queue_action(
    pending_repo, kind: str, student=None, period_month: str = "", *,
    amount: int = 0, method: str = "", parent_addr: str = "",
    student_id: str = "", student_name: str = "", file_id: str = "", file_type: str = "",
    comment: str = "",
):
    """Поставить решение в очередь. Ошибка листа не прерывает сценарий — только лог."""
    if pending_repo is None:
        return None
    sid = student.student_id if student is not None else student_id
    name = student.name if student is not None else student_name
    try:
        return await pending_repo.add(
            kind, sid, name, period_month, amount, method, parent_addr,
            file_id, file_type, comment,
        )
    except Exception as exc:                      # очередь — вспомогательная, платёж важнее
        logger.error("Очередь решений: не записали %s для %s: %s", kind, sid, exc)
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
        return await pending_repo.claim(action_id, status, decided_by_tg_id)
    except Exception as exc:
        logger.error("Очередь решений: не заняли %s: %s", action_id, exc)
        return True                               # лист недоступен — не блокируем оплату
