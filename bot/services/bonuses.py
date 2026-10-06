"""Премия педагогу: начисление сверх зарплаты месяца, выплачивается сразу.

Одно действие администратора («🎁 Премия» в боте и кабинете) пишет две строки:
`teacher_bonuses` — начисление (SalaryService показывает его строкой «Премия: …»
в зарплате педагога, «Зарплатах» и «Прибыли» — как зарплату) и парную строку
`teacher_payouts` с комментарием «премия: …» — факт выплаты, поэтому остаток
к выплате не меняется, а статус 🟢/🟡/🔴 остаётся прежним. Отмена премии
удаляет обе строки.
"""
from __future__ import annotations

import logging

from bot.repositories.teacher_bonus_repo import TeacherBonus
from bot.services import activity

logger = logging.getLogger(__name__)


def payout_comment(comment: str) -> str:
    """Комментарий парной выплаты: по нему премия видна и в списке выплат."""
    return f"премия: {comment}" if comment else "премия"


async def grant_bonus(
    bonus_repo, payout_repo, teacher_id: str, period: str, amount: int, comment: str, by_tg_id: int,
) -> TeacherBonus:
    """Начислить и сразу выплатить премию. Не записалось начисление — выплата снимается."""
    payout = await payout_repo.add(teacher_id, period, amount, by_tg_id, comment=payout_comment(comment))
    try:
        return await bonus_repo.add(teacher_id, period, amount, comment, by_tg_id, payout_id=payout.payout_id)
    except Exception:
        logger.exception("Премия %s %s %d ₽ не записалась — снимаем выплату %s",
                         teacher_id, period, amount, payout.payout_id)
        try:
            await payout_repo.delete(payout.payout_id)
        except Exception:
            logger.exception("Выплата %s осталась без начисления — удалите её в листе teacher_payouts",
                             payout.payout_id)
        raise


async def revoke_bonus(bonus_repo, payout_repo, bonus_id: str, by_tg_id: int) -> TeacherBonus | None:
    """Отменить премию: удалить начисление и парную выплату. None — такой премии нет."""
    bonus = await bonus_repo.get_by_id(bonus_id)
    if bonus is None:
        return None
    await bonus_repo.delete(bonus_id)
    if bonus.payout_id:
        await payout_repo.delete(bonus.payout_id)
    await activity.record(
        activity.PAYOUT,
        f"Премия отменена {bonus.amount} ₽: {bonus.teacher_id} · {bonus.period_month}"
        + (f" · {bonus.comment}" if bonus.comment else ""),
        actor=by_tg_id, ref=bonus_id,
    )
    return bonus
