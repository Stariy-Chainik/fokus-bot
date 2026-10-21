"""Выход ученика из группы: пометка «ушёл с месяца» вместо удаления строки.

Для абонементных групп удалять членство нельзя — иначе исчезнут начисления за
месяцы, когда ученик реально занимался, а сделанные оплаты станут переплатой.
Поэтому ставим `left_period`: с этого месяца абонемент не начисляется, прошлое
сохраняется. Для остальных групп строка удаляется, как раньше: там суммы берутся
из снимка занятия и от членства не зависят.
"""
from __future__ import annotations

import logging

from bot.models.enums import GroupBillingMode
from bot.utils.dates import current_period, next_period

logger = logging.getLogger(__name__)


async def is_subscription(group_id: str, group_repo) -> bool:
    group = await group_repo.get_by_id(group_id)
    return group is not None and group.billing_mode == GroupBillingMode.SUBSCRIPTION


def leave_options() -> list[tuple[str, str]]:
    """[(месяц, подпись)] — с какого месяца прекращать начисление абонемента."""
    this_month, nxt = current_period(), next_period()
    return [
        (this_month, f"С этого месяца ({_ru(this_month)}) — не оплачивает его"),
        (nxt, f"Со следующего ({_ru(nxt)}) — {_ru(this_month)} оплачивает"),
    ]


def _ru(period: str) -> str:
    from bot.utils.dates import display_period
    return display_period(period)


async def leave_group(student_id: str, group_id: str, left_period: str,
                      group_repo, student_group_repo) -> str:
    """Убрать ученика из группы. Возвращает 'marked' | 'removed' | 'missing'."""
    if await is_subscription(group_id, group_repo):
        ok = await student_group_repo.set_left_period(student_id, group_id, left_period)
        if ok:
            logger.info("Ученик %s помечен ушедшим из %s с %s", student_id, group_id, left_period)
            return "marked"
        return "missing"
    removed = await student_group_repo.remove(student_id, group_id)
    return "removed" if removed else "missing"
