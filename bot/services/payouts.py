"""Отмена выплаты зарплаты (решение владельца 01.11.2026: аванс Контаревой ушёл в ноябрь вместо октября,
а убрать строку из кабинета было нечем).

`cancel_payout` удаляет строку `teacher_payouts` и пишет событие в ленту; остаток к выплате педагогу
снова увеличивается. Парную выплату премии так снять нельзя — премия отменяется целиком
(`bonuses.revoke_bonus`), иначе начисление останется, а выплата пропадёт.
"""
from __future__ import annotations

from bot.repositories.teacher_payout_repo import TeacherPayout
from bot.services import activity


class BonusPayoutError(Exception):
    """Выплата принадлежит премии — отменяйте премию."""


async def cancel_payout(payout_repo, bonus_repo, payout_id: str, by_tg_id: int) -> TeacherPayout | None:
    """Удалить выплату. None — такой строки нет; BonusPayoutError — это выплата премии."""
    payout = next((p for p in await payout_repo.get_all() if p.payout_id == payout_id), None)
    if payout is None:
        return None
    if bonus_repo is not None and any(b.payout_id == payout_id for b in await bonus_repo.get_all()):
        raise BonusPayoutError(payout_id)
    if not await payout_repo.delete(payout_id):
        return None
    await activity.record(
        activity.PAYOUT,
        f"Выплата отменена {payout.amount} ₽: {payout.teacher_id} · {payout.period_month}"
        + (f" · {payout.comment}" if payout.comment else ""),
        actor=by_tg_id, ref=payout_id,
    )
    return payout
