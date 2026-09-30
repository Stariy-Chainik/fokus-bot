"""Оплаты месяца по филиалам → группам → ученикам (плитка «Не оплатили за …» у администратора).

Начисления и оплаты — из `PaymentService.compute_ledger_map` (те же цифры, что плитка на сводке).
Абонемент `SUB:gid` относится к своей группе. Начисление педагога раскладывается по занятиям:
групповое — в группу занятия, индивидуальное (и служебные revenue-share группы) — в раздел
«Индивидуальные» по педагогу. Оплата педагогу ложится на занятия так же, как ✓ в счёте
(`student_lesson_marks`); неполная оплата занятия — на первые неоплаченные по дате.
"""
from __future__ import annotations

from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX

IND = "IND"          # раздел индивидуальных занятий (вместо филиала)


def _bucket() -> dict:
    return {"accrued": 0, "paid": 0}


async def month_breakdown(dp, period: str) -> dict:
    from config.settings import settings
    payment_service = dp["payment_service"]
    ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
    groups = {g.group_id: g for g in await dp["group_repo"].get_all(include_archived=True)}
    branches = {b.branch_id: b.name for b in await dp["branch_repo"].get_all()}
    teachers = {t.teacher_id: t.name for t in await dp["teacher_repo"].get_all()}
    names = {s.student_id: s.name for s in await dp["student_repo"].get_all()}
    service = set(settings.revenue_share_group_map)

    # (вид, id группы | педагога) → ученик → {accrued, paid}
    cells: dict[tuple[str, str], dict[str, dict]] = {}

    def add(key: tuple[str, str], sid: str, accrued: int, paid: int) -> None:
        b = cells.setdefault(key, {}).setdefault(sid, _bucket())
        b["accrued"] += accrued
        b["paid"] += paid

    lesson_keys: dict[str, dict[str, tuple[int, int]]] = {}
    for (sid, key, ym), (accrued, paid) in ledger.items():
        if ym != period or accrued <= 0:
            continue
        paid = min(paid, accrued)
        if key.startswith(SUBSCRIPTION_KEY_PREFIX):
            add(("G", key[len(SUBSCRIPTION_KEY_PREFIX):]), sid, accrued, paid)
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
                key = ("G", ls.group_id) if ls.group_id and ls.group_id not in service else (IND, tid)
                got = m.amount if m.paid else max(0, min(left, m.amount))
                if not m.paid:
                    left -= got
                add(key, sid, m.amount, got)
                placed += m.amount
                got_total += got
            if accrued > placed:                   # занятие без педагога в справочнике и т.п. — к индивидуальным
                diff = accrued - placed
                add((IND, tid), sid, diff, max(0, min(paid - got_total, diff)))

    def rows_for(key: tuple[str, str]) -> dict:
        studs = []
        for sid, b in cells[key].items():
            rest = max(b["accrued"] - b["paid"], 0)
            studs.append({"id": sid, "name": names.get(sid, sid), "accrued": b["accrued"], "paid": b["paid"],
                          "rest": rest, "status": "paid" if not rest else "partial" if b["paid"] else "unpaid"})
        studs.sort(key=lambda x: (-x["rest"], x["name"]))
        kind, ident = key
        name = (groups[ident].name if ident in groups else ident) if kind == "G" else \
            f"Индивидуальные — {teachers.get(ident, ident)}"
        return {"id": f"{kind}:{ident}", "name": name, "students": studs,
                "accrued": sum(x["accrued"] for x in studs), "paid": sum(x["paid"] for x in studs),
                "rest": sum(x["rest"] for x in studs), "unpaid": sum(1 for x in studs if x["rest"])}

    out: dict[str, dict] = {}
    for key in cells:
        kind, ident = key
        bid = (groups[ident].branch_id if ident in groups else "?") if kind == "G" else IND
        br = out.setdefault(bid, {"id": bid, "name": "Индивидуальные занятия" if bid == IND
                                  else branches.get(bid, "Без филиала"), "groups": []})
        br["groups"].append(rows_for(key))
    result = []
    for br in out.values():
        br["groups"].sort(key=lambda g: (-g["rest"], g["name"]))
        br["accrued"] = sum(g["accrued"] for g in br["groups"])
        br["paid"] = sum(g["paid"] for g in br["groups"])
        br["rest"] = sum(g["rest"] for g in br["groups"])
        br["unpaid"] = len({s["id"] for g in br["groups"] for s in g["students"] if s["rest"]})
        result.append(br)
    result.sort(key=lambda b: (b["id"] == IND, b["name"]))
    return {"period": period, "branches": result,
            "accrued": sum(b["accrued"] for b in result), "paid": sum(b["paid"] for b in result),
            "rest": sum(b["rest"] for b in result)}
