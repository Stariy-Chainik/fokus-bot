"""Выход ученика из группы: пометка «ушёл с месяца» вместо удаления строки.

Для абонементных групп удалять членство нельзя — иначе исчезнут начисления за
месяцы, когда ученик реально занимался, а сделанные оплаты станут переплатой.
Поэтому ставим `left_period`: с этого месяца абонемент не начисляется, прошлое
сохраняется. Для остальных групп строка удаляется, как раньше: там суммы берутся
из снимка занятия и от членства не зависят.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

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


@dataclass
class LeaveSchoolResult:
    """Что сделано при уходе из школы: по каким группам пометка ухода, где строка удалена."""
    left_period: str
    marked: list = field(default_factory=list)      # абонементные группы — `left_period`
    removed: list = field(default_factory=list)     # остальные — строка членства удалена

    @property
    def groups(self) -> list:
        return self.marked + self.removed


async def leave_school(student_id: str, left_period: str, group_repo, student_group_repo,
                       only_groups=None, who: str = "", actor: int = 0) -> LeaveSchoolResult:
    """«Удалить ученика» в карточке = ушёл из школы (решение владельца 21.10.2026): снять со всех групп,
    карточку, занятия и оплаты оставить. Абонементные группы — пометка `left_period`, остальные — строка
    удаляется (как `leave_group`). `only_groups` — ограничить своими группами (педагог: чужие направления не
    трогает). Одно событие в ленте от имени `actor`; репозитории пишут свои по каждой группе."""
    gids = await student_group_repo.get_groups_for_student(student_id)
    if only_groups is not None:
        gids = [g for g in gids if g in only_groups]
    result = LeaveSchoolResult(left_period)
    for gid in gids:
        r = await leave_group(student_id, gid, left_period, group_repo, student_group_repo)
        if r == "marked":
            result.marked.append(gid)
        elif r == "removed":
            result.removed.append(gid)
    if result.groups:
        from bot.services import activity
        await activity.record(activity.STUDENT, f"Ученик {student_id} ушёл из школы с {left_period}: "
                              + ", ".join(result.groups) + (f" ({who})" if who else ""), actor=actor, ref=student_id)
        logger.info("Ученик %s ушёл из школы с %s: пометка %s, убран %s (%s)",
                    student_id, left_period, result.marked, result.removed, who or "админ")
    return result
