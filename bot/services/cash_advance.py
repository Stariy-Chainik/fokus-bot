"""«💸 Оставить педагогу как аванс» (решение владельца 12.11.2026): родитель отдал наличные педагогу, а педагог
не сдаёт их в школу — деньги идут ему авансом в счёт зарплаты. Одно действие администратора на заявке
«Наличные у педагога» (`pending_actions.held_by`): оплата ученика зачитывается как обычно (родителю — «оплата
подтверждена»), и в `teacher_payouts` пишется выплата педагогу на ту же сумму за **текущий месяц** с пометкой,
от чьих родителей деньги. Педагог видит её в «Зарплата → Выплаты школы» как аванс и получает сообщение,
что сдавать деньги не нужно. Ошибочный аванс снимается 🗑 в карточке педагога, как любая выплата.

Кабинет — `POST /inbox/{id}/decide {approve, advance}` (bot/api/admin_inbox.py), Telegram — кнопка `padv:` в
сообщении «💵 Наличные у педагога» (bot/handlers/client/my_bills/payment.py).
"""
from __future__ import annotations

import logging

from bot.repositories.teacher_payout_repo import TeacherPayout
from bot.utils.dates import current_period, period_label

logger = logging.getLogger(__name__)


def advance_comment(student_name: str, period_month: str) -> str:
    return f"наличные от родителя: {student_name}, {period_label(period_month).lower()}"


async def keep_as_advance(payout_repo, user_repo, bot, action, student_name: str, amount: int,
                          by_tg_id: int) -> TeacherPayout:
    """Выплата педагогу `action.held_by` на `amount` за текущий месяц + сообщение ему."""
    period = current_period()
    payout = await payout_repo.add(action.held_by, period, amount, by_tg_id,
                                   comment=advance_comment(student_name, action.period_month))
    logger.info("Наличные %s (%d руб.) оставлены педагогу %s авансом за %s — админ %s",
                action.action_id, amount, action.held_by, period, by_tg_id)
    if bot is not None and user_repo is not None:
        text = (f"💸 Наличные {amount} ₽ от родителя ({student_name}, {period_label(action.period_month).lower()}) "
                f"оставлены вам авансом в счёт зарплаты за {period_label(period).lower()}.\n"
                f"Сдавать их в школу не нужно. Аванс виден в кабинете: «Зарплата» → «Выплаты школы».")
        for u in await user_repo.get_all():
            if u.teacher_id == action.held_by:
                try:
                    await bot.send_message(u.tg_id, text)
                except Exception as exc:          # аванс записан — сообщение вспомогательное
                    logger.warning("Педагогу %s не ушло сообщение об авансе: %s", u.tg_id, exc)
    return payout
