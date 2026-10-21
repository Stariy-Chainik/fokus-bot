"""Архив ученика — «🗑 Удалить ученика» в карточке у администратора и педагога (решение владельца 21.10.2026).

Один сценарий для обеих ролей: ученик **уходит из школы**, а не стирается. Что происходит:

- снимается со всех групп — у каждой строки членства ставится `left_period` = текущий месяц (строки не
  удаляются: на них держатся начисления прошлых месяцев и возврат из архива);
- родители отвязываются (и от карточки клиента, если по ней шли уведомления), партнёр отвязывается,
  кабинет спортсмена закрывается;
- в `students.archived` пишется дата ухода — такого ученика нет в списках, поиске, составах и начислениях
  (`StudentRepository.get_all()` без `include_archived`), долг в должниках не считается;
- история занятий, оплат и событий остаётся, карточка открывается по номеру (`get_by_id`) и из фильтра «📦 Архив»;
- попадает в «Ушли из школы» на сводке (`student_flow`: `left_period` = месяц, день — из событий).

Долг не мешает, но показывается до подтверждения (`archive_preview` → `debt`): без `confirm_debt` при долге
`archive_student` не выполняется — кнопка подтверждения в листе. Возврат — `unarchive_student`: снимает
архив и пометку ухода с членств, помеченных в месяц архивации; родителей и партнёра заново привязывает
администратор (или родитель — по ссылке группы).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from bot.services import activity
from bot.services.parent_unlink import drop_client_addr
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX
from bot.utils.dates import current_period

logger = logging.getLogger(__name__)


@dataclass
class Debt:
    total: int = 0
    sub: int = 0        # абонементы
    group: int = 0      # групповые по посещению
    ind: int = 0        # индивидуальные и парные

    def as_dict(self) -> dict:
        return {"total": self.total, "sub": self.sub, "group": self.group, "ind": self.ind}


@dataclass
class ArchivePreview:
    student_id: str
    name: str
    groups: list = field(default_factory=list)      # [(gid, name)] — активные членства, откуда снимем
    parents: int = 0
    partner: str | None = None
    athlete: bool = False
    debt: Debt = field(default_factory=Debt)
    archived: str = ""

    def as_dict(self) -> dict:
        return {"student": {"id": self.student_id, "name": self.name},
                "groups": [{"id": g, "name": n} for g, n in self.groups],
                "parents": self.parents, "partner": self.partner, "athlete": self.athlete,
                "debt": self.debt.as_dict(), "archived": self.archived}


@dataclass
class ArchiveResult:
    student_id: str
    name: str
    date: str
    period: str
    groups: list = field(default_factory=list)      # [(gid, name)]
    parents: int = 0
    partner: str | None = None
    debt: Debt = field(default_factory=Debt)

    def as_dict(self) -> dict:
        return {"ok": True, "id": self.student_id, "name": self.name, "date": self.date, "period": self.period,
                "groups": [{"id": g, "name": n} for g, n in self.groups], "parents": self.parents,
                "partner": self.partner, "debt": self.debt.as_dict()}


async def student_debt(payment_service, student_id: str, since: str | None) -> Debt:
    """Долг ученика за все месяцы с `since` с разбивкой «абонемент · групповые · индивидуальные».
    Итог — по карте начислений (как «Должники»); занятия делятся по отметкам оплаты занятий
    (`student_lesson_marks`): неоплаченные индивидуальные — `ind`, остальное — `group`."""
    ledger = await payment_service.compute_ledger_map(since_period=since)
    debt = Debt()
    months_with_lessons: set = set()
    for (sid, key, ym), (accrued, paid) in ledger.items():
        if sid != student_id or accrued <= paid:
            continue
        rest = accrued - paid
        debt.total += rest
        if key.startswith(SUBSCRIPTION_KEY_PREFIX):
            debt.sub += rest
        else:
            months_with_lessons.add(ym)
    for ym in sorted(months_with_lessons):
        month = await payment_service.student_lesson_marks(student_id, ym)
        for ls in month.lessons:
            m = month.mark(ls.lesson_id)
            if m.amount > 0 and not m.paid and not ls.group_id:
                debt.ind += m.amount
    debt.ind = min(debt.ind, debt.total - debt.sub)
    debt.group = max(debt.total - debt.sub - debt.ind, 0)
    return debt


async def archive_preview(student_id: str, *, student_repo, student_group_repo, group_repo,
                          payment_service, since: str | None) -> ArchivePreview | None:
    s = await student_repo.get_by_id(student_id)
    if s is None:
        return None
    names = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}
    gids = await student_group_repo.get_groups_for_student(student_id)
    partner = await student_repo.get_by_id(s.partner_id) if s.partner_id else None
    return ArchivePreview(
        student_id=s.student_id, name=s.name,
        groups=[(g, names.get(g, g)) for g in sorted(gids, key=lambda g: str(names.get(g, g)))],
        parents=len(s.parent_addrs), partner=partner.name if partner else None, athlete=s.is_athlete,
        debt=await student_debt(payment_service, student_id, since), archived=s.archived,
    )


async def archive_student(student_id: str, *, student_repo, student_group_repo, group_repo, client_repo,
                          payment_service, since: str | None, actor: int = 0, who: str = "",
                          confirm_debt: bool = False) -> ArchiveResult | str:
    """Ушёл из школы. Возвращает результат или код: 'not_found' | 'already' | 'debt' (долг есть, а
    подтверждения нет — вызывающий показывает сумму и просит подтвердить)."""
    preview = await archive_preview(student_id, student_repo=student_repo, student_group_repo=student_group_repo,
                                    group_repo=group_repo, payment_service=payment_service, since=since)
    if preview is None:
        return "not_found"
    if preview.archived:
        return "already"
    if preview.debt.total > 0 and not confirm_debt:
        return "debt"
    s = await student_repo.get_by_id(student_id)
    period, today = current_period(), date.today().isoformat()
    for gid, _name in preview.groups:
        await student_group_repo.set_left_period(student_id, gid, period)
    if s.partner_id:
        await student_repo.clear_partner(student_id)
    for addr in list(s.parent_addrs):
        if await student_repo.remove_parent(student_id, addr):
            await drop_client_addr(student_repo, client_repo, s, addr)
    if s.athlete_tg_id:
        await student_repo.set_athlete_tg_id(student_id, None)
    if s.athlete_max_id:
        await student_repo.set_athlete_max_id(student_id, None)
    await student_repo.set_archived(student_id, today)
    await activity.record(
        activity.STUDENT,
        f"Ученик {student_id} {s.name} ушёл из школы (в архив) с {period}"
        + (": " + ", ".join(g for g, _n in preview.groups) if preview.groups else "")
        + (f"; неоплачено {preview.debt.total} ₽" if preview.debt.total else "")
        + (f" ({who})" if who else ""),
        actor=actor, ref=student_id)
    logger.info("Ученик %s в архиве с %s: группы %s, родителей %d, партнёр %s, долг %d (%s)",
                student_id, today, [g for g, _n in preview.groups], preview.parents, preview.partner,
                preview.debt.total, who or f"админ {actor}")
    return ArchiveResult(student_id, s.name, today, period, preview.groups, preview.parents, preview.partner,
                         preview.debt)


async def unarchive_student(student_id: str, *, student_repo, student_group_repo, actor: int = 0) -> str | None:
    """Вернуть из архива: снять дату и пометку ухода с членств, помеченных в месяц архивации.
    None — успех, иначе 'not_found' | 'not_archived'."""
    s = await student_repo.get_by_id(student_id)
    if s is None:
        return "not_found"
    if not s.archived:
        return "not_archived"
    period = s.archived[:7]
    restored = []
    for (sid, gid), sg in (await student_group_repo.get_membership_map()).items():
        if sid == student_id and sg.left_period == period:
            await student_group_repo.set_left_period(student_id, gid, "")
            restored.append(gid)
    await student_repo.set_archived(student_id, "")
    await activity.record(activity.STUDENT, f"Ученик {student_id} {s.name} возвращён из архива"
                          + (": " + ", ".join(restored) if restored else ""), actor=actor, ref=student_id)
    logger.info("Ученик %s возвращён из архива, группы %s (актор %s)", student_id, restored, actor)
    return None

