"""Уведомления педагогам об оплатах учеников их групп (FULL_BILL_TEACHER_IDS — все группы
педагога, PAYMENT_NOTIFY_GROUPS — только перечисленные).

Одна точка для всех путей зачёта: ЮКасса, очередь решений, кабинеты админа и педагога,
кнопки в боте — все они идут через PaymentService, а он зовёт `payment_received()`.
Отправка — фоновой задачей: оплата не ждёт Telegram. Без `setup()` (тесты, скрипты) — no-op.
"""
from __future__ import annotations

import asyncio
import logging

from bot.services import payment_methods
from config.settings import settings

logger = logging.getLogger(__name__)

_deps: dict = {}
_tasks: set = set()


def setup(tg_bot, user_repo, teacher_group_repo, student_group_repo, student_repo) -> None:
    _deps.update(bot=tg_bot, users=user_repo, tg=teacher_group_repo, sg=student_group_repo, students=student_repo)


def payment_received(student_id: str, period: str, amount: int, method: str, actor: int,
                     student_name: str = "") -> None:
    """Запланировать уведомление; вызывается сразу после зачёта оплаты."""
    if not _deps.get("bot") or amount <= 0 or not (settings.full_bill_teacher_id_set
                                                    or settings.payment_notify_group_map):
        return
    task = asyncio.get_running_loop().create_task(
        _send(student_id, period, amount, method, int(actor or 0), student_name))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def recipients(student_id: str, actor: int) -> list[int]:
    """tg_id педагогов, которым интересна оплата ученика; кто отметил сам — не уведомляем.

    С полным счётом — если ученик в любой группе педагога; по списку групп — если в одной из них.
    """
    groups = set(await _deps["sg"].get_groups_for_student(student_id))
    full, by_group = settings.full_bill_teacher_id_set, settings.payment_notify_group_map
    out: list[int] = []
    for u in await _deps["users"].get_all():
        tid = u.teacher_id
        if not tid or u.tg_id == actor or u.tg_id in out:
            continue
        if groups & by_group.get(tid, set()) or (
                tid in full and groups & set(await _deps["tg"].get_groups_for_teacher(tid))):
            out.append(u.tg_id)
    return out


async def _send(student_id: str, period: str, amount: int, method: str, actor: int, name: str) -> None:
    from bot.utils.dates import period_label
    try:
        to = await recipients(student_id, actor)
        if not to:
            return
        if not name:
            s = await _deps["students"].get_by_id(student_id)
            name = s.name if s else student_id
        text = (f"💳 Оплата {amount} ₽ — {name}, {period_label(period).lower()}\n"
                f"{payment_methods.label(method, confirmed_by_tg_id=actor)}")
        for tg_id in to:
            try:
                await _deps["bot"].send_message(tg_id, text)
            except Exception as exc:
                logger.warning("Педагогу %s не ушло уведомление об оплате: %s", tg_id, exc)
    except Exception as exc:                        # уведомление вспомогательное
        logger.warning("Уведомление педагогам об оплате %s: %s", student_id, exc)
