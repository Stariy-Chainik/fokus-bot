"""Оплаты месяца по филиалам → группам → ученикам (плитка «Не оплатили за …» у администратора).

Долг формирует ученик: абонемент, групповые и индивидуальные занятия (решение владельца 01.10.2026).
Начисления и оплаты — из `PaymentService.compute_ledger_map` (те же цифры, что плитка на сводке).
Раскладка по группам:
- абонемент `SUB:gid` — в свою группу;
- групповое занятие — в группу, где оно прошло;
- индивидуальное (и служебные revenue-share группы) — в «домашнюю» группу ученика: его группу у того
  же педагога, иначе первую его группу месяца; нет групп — раздел «Без группы».
Оплата педагогу ложится на занятия так же, как ✓ в счёте (`student_lesson_marks`); неполная оплата
занятия — на первые неоплаченные по дате.
"""
from __future__ import annotations

from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX

NO_GROUP = "NOGROUP"      # ученик без групп: раздел вместо филиала
PARTS = ("sub", "group", "ind")      # абонемент / групповые / индивидуальные


def _cell() -> dict:
    return {"accrued": 0, "paid": 0, **{p: 0 for p in PARTS}}


async def month_breakdown(dp, period: str) -> dict:
    from config.settings import settings
    payment_service = dp["payment_service"]
    ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
    groups = {g.group_id: g for g in await dp["group_repo"].get_all(include_archived=True)}
    branches = {b.branch_id: b.name for b in await dp["branch_repo"].get_all()}
    names = {s.student_id: s.name for s in await dp["student_repo"].get_all()}
    service = set(settings.revenue_share_group_map)
    membership = await dp["student_group_repo"].get_membership_map()
    student_groups: dict[str, list[str]] = {}
    for (sid, gid), m in membership.items():
        if gid in groups and gid not in service and m.covers(period):
            student_groups.setdefault(sid, []).append(gid)
    for gids in student_groups.values():
        gids.sort(key=lambda g: groups[g].name)
    teacher_groups: dict[str, set] = {}
    for tg in await dp["teacher_group_repo"].get_all():
        teacher_groups.setdefault(tg.teacher_id, set()).add(tg.group_id)

    def home_group(sid: str, tid: str) -> str:
        """Куда отнести индивидуальные ученика у педагога tid."""
        mine = student_groups.get(sid, [])
        same = [g for g in mine if g in teacher_groups.get(tid, set())]
        return (same or mine or [NO_GROUP])[0]

    # группа → ученик → суммы
    cells: dict[str, dict[str, dict]] = {}

    def add(gid: str, sid: str, part: str, accrued: int, paid: int) -> None:
        c = cells.setdefault(gid, {}).setdefault(sid, _cell())
        c["accrued"] += accrued
        c["paid"] += paid
        c[part] += accrued

    lesson_keys: dict[str, dict[str, tuple[int, int]]] = {}
    for (sid, key, ym), (accrued, paid) in ledger.items():
        if ym != period or accrued <= 0:
            continue
        paid = min(paid, accrued)
        if key.startswith(SUBSCRIPTION_KEY_PREFIX):
            add(key[len(SUBSCRIPTION_KEY_PREFIX):], sid, "sub", accrued, paid)
        else:
            lesson_keys.setdefault(sid, {})[key] = (accrued, paid)

    for sid, keys in lesson_keys.items():
        month = await payment_service.student_lesson_marks(sid, period)
        chrono = sorted(month.lessons, key=lambda x: (x.date, x.lesson_id))
        for tid, (accrued, paid) in keys.items():
            items = [(ls, month.mark(ls.lesson_id)) for ls in chrono if ls.teacher_id == tid]
            items = [(ls, m) for ls, m in items if m.amount > 0]
            left = paid - sum(m.amount for _ls, m in items if m.paid)       # неполная оплата занятия
            placed = got_total = 0
            for ls, m in items:
                in_group = bool(ls.group_id) and ls.group_id not in service
                gid = ls.group_id if in_group else home_group(sid, tid)
                got = m.amount if m.paid else max(0, min(left, m.amount))
                if not m.paid:
                    left -= got
                add(gid, sid, "group" if in_group else "ind", m.amount, got)
                placed += m.amount
                got_total += got
            if accrued > placed:                   # занятие без педагога в справочнике и т.п.
                diff = accrued - placed
                add(home_group(sid, tid), sid, "ind", diff, max(0, min(paid - got_total, diff)))

    # долг ученика по всем группам месяца: в карточке группы видна только её часть, а в счёте — весь,
    # поэтому у строки ученика подпись «всего N» (решение владельца 05.10.2026, случай Ким Алины)
    total_rest: dict[str, int] = {}
    for studs_in in cells.values():
        for sid, c in studs_in.items():
            total_rest[sid] = total_rest.get(sid, 0) + max(c["accrued"] - c["paid"], 0)

    def group_row(gid: str) -> dict:
        studs = []
        for sid, c in cells[gid].items():
            rest = max(c["accrued"] - c["paid"], 0)
            studs.append({"id": sid, "name": names.get(sid, sid), "accrued": c["accrued"], "paid": c["paid"],
                          "rest": rest, "totalRest": total_rest.get(sid, rest),
                          "status": "paid" if not rest else "partial" if c["paid"] else "unpaid",
                          **{p: c[p] for p in PARTS}})
        studs.sort(key=lambda x: (-x["rest"], x["name"]))
        name = groups[gid].name if gid in groups else "Без группы"
        return {"id": gid, "name": name, "students": studs,
                "accrued": sum(x["accrued"] for x in studs), "paid": sum(x["paid"] for x in studs),
                "rest": sum(x["rest"] for x in studs), "unpaid": sum(1 for x in studs if x["rest"])}

    out: dict[str, dict] = {}
    for gid in cells:
        bid = groups[gid].branch_id if gid in groups else NO_GROUP
        br = out.setdefault(bid, {"id": bid, "name": "Без группы" if bid == NO_GROUP
                                  else branches.get(bid, "Без филиала"), "groups": []})
        br["groups"].append(group_row(gid))
    result = []
    for br in out.values():
        br["groups"].sort(key=lambda g: (-g["rest"], g["name"]))
        br["accrued"] = sum(g["accrued"] for g in br["groups"])
        br["paid"] = sum(g["paid"] for g in br["groups"])
        br["rest"] = sum(g["rest"] for g in br["groups"])
        br["unpaid"] = len({s["id"] for g in br["groups"] for s in g["students"] if s["rest"]})
        result.append(br)
    result.sort(key=lambda b: (b["id"] == NO_GROUP, b["name"]))
    return {"period": period, "branches": result,
            "accrued": sum(b["accrued"] for b in result), "paid": sum(b["paid"] for b in result),
            "rest": sum(b["rest"] for b in result)}
