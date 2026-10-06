"""Кабинет администратора, этап 2: должники (напоминание), прибыль, зарплаты, выплаты, история оплат.

Маршруты регистрируются из bot/api/admin.py под тем же префиксом и той же проверкой роли.
"""
from __future__ import annotations

import logging
from datetime import date

from aiohttp import web

from bot.models.enums import LessonType, PaymentStatus
from bot.services.payment_ledger import StudentMonthLessons
from bot.services.payment_service import build_debtor_rows, period_collection, teacher_collection
from bot.services.pending_queue import awaiting_periods
from bot.services.profit_service import calculate_profit_lesson
from bot.utils.attendees import parse_attendees
from bot.utils.dates import current_period, display_period
from bot.utils.locks import InProgressGuard
from config.settings import settings

logger = logging.getLogger(__name__)
_reminding = InProgressGuard()


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _dp_get(dp, key: str):
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


def _profit_rows(summary) -> list[dict]:
    return [{
        "teacherId": r.teacher_id, "name": r.teacher_name, "income": r.income, "salary": r.salary,
        "owner": r.owner, "ownerIncome": r.owner_income, "rent": r.rent, "rentLessons": r.rent_lessons,
        "groupLessons": r.group_lessons, "individualLessons": r.individual_lessons,
        "profit": r.profit, "margin": r.margin_percent,
    } for r in summary.teacher_rows]


def _profit_totals(s) -> dict:
    return {
        "lessonIncome": s.lesson_income, "subscriptionIncome": s.subscription_income,
        "manualIncome": s.manual_income, "manualExpenses": s.manual_expenses, "totalIncome": s.total_income,
        "salary": s.salary, "ownerIncome": s.owner_income, "rentIncome": s.rent_income, "rentLessons": s.rent_lessons,
        "totalExpenses": s.total_expenses, "profit": s.profit, "isEmpty": s.is_empty,
    }


def _salary_line(ln) -> dict:
    return {"date": ln.date, "kind": ln.kind, "label": ln.label, "minutes": ln.minutes, "amount": ln.amount,
            "lessonId": ln.lesson_id, "groupId": ln.group_id}


async def _read_json(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def register_finance_routes(app: web.Application, dp, guard, prefix: str) -> None:
    teacher_repo = dp["teacher_repo"]
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    lesson_repo = dp["lesson_repo"]
    payment_repo = dp["payment_repo"]
    finance_repo = dp["finance_entry_repo"]
    payout_repo = dp["payout_repo"]
    override_repo = dp["salary_override_repo"]
    profit_service = dp["profit_service"]
    salary_service = dp["salary_service"]
    payment_service = dp["payment_service"]
    teacher_group_repo = dp["teacher_group_repo"]

    async def _debtor_rows():
        debt_map = await payment_service.compute_debt_map(since_period=settings.debtors_since_period or None)
        students = {s.student_id: s for s in await student_repo.get_all()}
        return build_debtor_rows(debt_map, students, current_period())

    # ── должники: напомнить всем ─────────────────────────────────────────
    async def debtors_remind(request: web.Request, user) -> web.Response:
        notifier = _dp_get(dp, "notifier")
        if notifier is None:
            return _json({"error": "bot_unavailable"}, status=503)
        key = str(user.tg_id)
        if key in _reminding:
            return _json({"error": "in_progress"}, status=409)
        _reminding.add(key)
        try:
            current = current_period()
            rows = await _debtor_rows()
            # родитель сообщил об оплате (наличные у педагога, чек) — ждём решения, не напоминаем
            awaiting = await awaiting_periods(_dp_get(dp, "pending_repo"))
            waiting = {d.student.student_id for d in rows
                       if any((d.student.student_id, p) in awaiting for p in d.periods if p < current)}
            rows = [d for d in rows if d.student.student_id not in waiting]
            targets = [d for d in rows if d.closed_total > 0 and d.has_parent]
            skipped = sum(1 for d in rows if d.closed_total > 0 and not d.has_parent)
            sent = failed = 0
            for d in targets:
                period_lines = [f"  • {display_period(p)}: {amt} ₽" for p, amt in d.periods.items() if p < current]
                text = (
                    "🔔 <b>Напоминание об оплате</b>\n\n"
                    f"Ученик: <b>{d.student.name}</b>\n"
                    f"Задолженность: <b>{d.closed_total} ₽</b>\n"
                    + "\n".join(period_lines)
                    + "\n\nДетали и оплата — в разделе «💳 Мои счета»."
                )
                if await notifier.send_many(d.student.parent_addrs, text) > 0:
                    sent += 1
                else:
                    failed += 1
        finally:
            _reminding.discard(key)
        logger.info("Mini App: напоминания о долгах от %s — доставлено %d, не доставлено %d", user.tg_id, sent, failed)
        return _json({"sent": sent, "failed": failed, "skipped": skipped, "awaiting": len(waiting),
                      "total": sum(d.closed_total for d in targets)})

    # ── прибыль ──────────────────────────────────────────────────────────
    def _month_payload(period: str, s) -> dict:
        return {
            "period": period, "rows": _profit_rows(s),
            "subscriptions": [{"groupId": x.group_id, "groupName": x.group_name, "students": x.billed_students, "income": x.income}
                              for x in s.subscription_rows],
            "finance": [{"id": e.entry_id, "kind": e.kind, "title": e.title, "amount": e.amount} for e in s.finance_entries],
            "totals": _profit_totals(s),
        }

    async def profit(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        return _json(_month_payload(period, await profit_service.get_month_summary(period)))

    async def profit_breakdown(request: web.Request, user) -> web.Response:
        """Откуда сложились выручка и прибыль месяца: составляющие + занятия по дням."""
        period = request.query.get("ym") or current_period()
        payload = _month_payload(period, await profit_service.get_month_summary(period))
        payload["days"] = [{"date": d.date, "income": d.income, "salary": d.salary, "rent": d.rent, "ownerIncome": d.owner_income,
                            "profit": d.profit, "lessons": d.lessons} for d in await profit_service.get_day_breakdown(period)]
        return _json(payload)

    async def profit_day(request: web.Request, user) -> web.Response:
        day = request.query.get("date") or date.today().isoformat()
        if len(day) != 10:
            return _json({"error": "bad_request"}, status=400)
        s = await profit_service.get_lesson_summary(day)
        return _json({"date": day, "rows": _profit_rows(s), "totals": _profit_totals(s)})

    async def _profit_units(period: str) -> list[dict]:
        """Прибыль по единицам (решение владельца 06.10.2026): группа по посещению — одна строка,
        индивидуальные педагога — одна строка; сразу прибыль, тап — как сложилась. Абонементные группы
        считаются месяцем и здесь не участвуют (их блок — «Абонементы»)."""
        teachers = {t.teacher_id: t for t in await teacher_repo.get_all()}
        groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        units: dict[str, dict] = {}
        for ls in await lesson_repo.get_all():
            if not ls.date.startswith(period):
                continue
            t = teachers.get(ls.teacher_id)
            if t is None:
                continue
            row = calculate_profit_lesson(ls, t)
            if row is None:
                continue
            service = ls.group_id in settings.revenue_share_group_map        # техгруппа «Индивидуальные — …»
            if ls.type == LessonType.GROUP and ls.group_id and not service:
                key, kind = f"g:{ls.group_id}", "group"
                title = groups[ls.group_id].name if ls.group_id in groups else "Группа"
                sub = t.name
            else:
                key, kind = f"t:{ls.teacher_id}", "individual"
                title, sub = f"Индивидуальные — {t.name}", ""
            u = units.setdefault(key, {"key": key, "kind": kind, "title": title, "sub": sub,
                                       "groupId": ls.group_id if kind == "group" else "", "teacherId": ls.teacher_id,
                                       "income": 0, "salary": 0, "rent": 0, "ownerIncome": 0, "lessons": [], "owner": row.owner_income > 0})
            u["income"] += row.income
            u["salary"] += row.salary
            u["rent"] += row.rent
            u["ownerIncome"] += row.owner_income
            who = ([names.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
                   if ls.type == LessonType.GROUP else
                   [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n])
            u["lessons"].append({"lessonId": ls.lesson_id, "date": ls.date, "lessonType": ls.type.value,
                                 "durationMin": ls.duration_min, "income": row.income, "salary": row.salary,
                                 "rent": row.rent, "ownerIncome": row.owner_income, "students": who,
                                 "groupId": ls.group_id or "", "groupName": groups[ls.group_id].name if ls.group_id in groups else "",
                                 "teacher": t.name})
        out = []
        for u in units.values():
            u["lessons"].sort(key=lambda x: (x["date"], x["lessonId"]))
            u["count"] = len(u["lessons"])
            u["profit"] = u["income"] - u["salary"]
            out.append(u)
        out.sort(key=lambda u: (u["kind"] != "group", -u["profit"]))
        return out

    async def profit_units(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        units = await _profit_units(period)
        return _json({"period": period, "units": [{k: v for k, v in u.items() if k != "lessons"} for u in units]})

    async def profit_unit(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        key = request.query.get("key") or ""
        unit = next((u for u in await _profit_units(period) if u["key"] == key), None)
        if unit is None:
            return _json({"error": "not_found"}, status=404)
        return _json({"period": period, **unit})

    async def profit_subscription(request: web.Request, user) -> web.Response:
        """Состав абонементной группы за месяц: начислено / оплачено по каждому ученику."""
        gid = request.match_info["gid"]
        period = request.query.get("ym") or current_period()
        rows = await payment_service.subscription_group_detail(gid, period)
        group = await group_repo.get_by_id(gid)
        if rows is None or group is None:
            return _json({"error": "not_found"}, status=404)
        names = {s.student_id: s.name for s in await student_repo.get_all()}

        def status(r) -> str:
            if r.accrued == 0:
                return "paid" if r.paid else "exempt"
            return "paid" if r.paid >= r.accrued else "partial" if r.paid else "unpaid"

        students = sorted(({
            "studentId": r.student_id, "name": names.get(r.student_id, r.student_id), "accrued": r.accrued, "paid": r.paid,
            "remainder": max(r.accrued - r.paid, 0), "active": r.active, "status": status(r),
        } for r in rows), key=lambda x: x["name"])
        return _json({
            "groupId": gid, "groupName": group.name, "period": period, "price": group.price_full, "students": students,
            "accrued": sum(r.accrued for r in rows), "paid": sum(r.paid for r in rows),
        })

    async def profit_teacher(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        period = request.query.get("period") or current_period()
        d = await profit_service.get_teacher_detail(tid, period)
        if d is None:
            return _json({"error": "not_found"}, status=404)
        lessons = {ls.lesson_id: ls for ls in await lesson_repo.get_by_teacher_and_period(tid, period)}
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}
        names = {s.student_id: s.name for s in await student_repo.get_all()}

        def who(ls) -> list[str]:
            if ls is None:
                return []
            if ls.type == LessonType.GROUP:
                return [names.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
            return [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]

        return _json({
            "teacherId": d.teacher_id, "name": d.teacher_name, "period": d.period, "owner": d.owner,
            "lessons": [{"lessonId": r.lesson_id, "date": r.date, "lessonType": getattr(r.lesson_type, "value", str(r.lesson_type)),
                         "durationMin": r.duration_min, "income": r.income, "salary": r.salary, "rent": r.rent,
                         "ownerIncome": r.owner_income, "students": who(lessons.get(r.lesson_id)),
                         "groupId": getattr(lessons.get(r.lesson_id), "group_id", "") or "",
                         "groupName": groups.get(getattr(lessons.get(r.lesson_id), "group_id", ""), "")} for r in d.lessons],
            "income": d.income, "salary": d.salary, "ownerIncome": d.owner_income, "profit": d.profit, "margin": d.margin_percent,
        })

    async def finance_add(request: web.Request, user) -> web.Response:
        body = await _read_json(request)
        if body is None:
            return _json({"error": "bad_request"}, status=400)
        period, kind, title, amount = body.get("periodMonth"), body.get("kind"), (body.get("title") or "").strip(), body.get("amount")
        if not isinstance(period, str) or len(period) != 7 or kind not in ("income", "expense") or not title \
                or not isinstance(amount, int) or amount <= 0:
            return _json({"error": "bad_request"}, status=400)
        entry = await finance_repo.add(period, kind, title, amount)
        return _json({"id": entry.entry_id})

    async def finance_delete(request: web.Request, user) -> web.Response:
        ok = await finance_repo.delete(request.match_info["eid"])
        return _json({"ok": ok}, status=200 if ok else 404)

    # ── зарплаты и выплаты ───────────────────────────────────────────────
    async def _paid_by_teacher(period: str) -> dict[str, int]:
        paid: dict[str, int] = {}
        for p in await payout_repo.get_by_period(period):
            paid[p.teacher_id] = paid.get(p.teacher_id, 0) + p.amount
        return paid

    def _owner_paid(tid: str, accrued: int, paid: int) -> int:
        """Руководитель (OWNER_TEACHER_IDS) зарплату себе не выплачивает — она остаётся в прибыли,
        поэтому в «Зарплатах» и «Выплатах» он всегда «выплачено» (решение владельца 03.10.2026)."""
        return max(accrued, paid) if tid in settings.owner_teacher_id_set else paid

    async def _sub_groups_by_teacher(period: str) -> dict[str, set[str]]:
        """Абонементы каких групп относить к педагогу: группы, где он вёл занятия в месяце;
        без занятий — его группы из teacher_groups."""
        by_lessons: dict[str, set[str]] = {}
        for ls in await lesson_repo.get_all():
            if ls.group_id and ls.date.startswith(period):
                by_lessons.setdefault(ls.teacher_id, set()).add(ls.group_id)
        by_link: dict[str, set[str]] = {}
        for tg in await teacher_group_repo.get_all():
            by_link.setdefault(tg.teacher_id, set()).add(tg.group_id)
        return {tid: by_lessons.get(tid) or by_link.get(tid, set()) for tid in set(by_lessons) | set(by_link)}

    async def _unpaid_lessons(period: str, tid: str, col: dict[str, tuple[int, int]],
                              cache: dict[str, StudentMonthLessons]) -> dict[str, int]:
        """Сколько занятий педагога не оплачено у каждого ученика с остатком (отметка та же, что в счёте)."""
        out: dict[str, int] = {}
        for sid, (a, p) in col.items():
            if a <= p:
                continue
            month = cache.get(sid)
            if month is None:
                month = cache[sid] = await payment_service.student_lesson_marks(sid, period)
            n = sum(1 for ls in month.lessons
                    if ls.teacher_id == tid and month.mark(ls.lesson_id).amount > 0 and not month.mark(ls.lesson_id).paid)
            if n:
                out[sid] = n
        return out

    def _collection_totals(col: dict[str, tuple[int, int]], unpaid_lessons: dict[str, int],
                           sub_groups: set[str], ledger, period: str) -> dict:
        accrued = sum(a for a, _p in col.values())
        paid = sum(p for _a, p in col.values())
        sub_keys = {f"SUB:{g}" for g in sub_groups}
        unpaid_subs = sum(1 for (sid, key, ym), (a, p) in ledger.items()
                          if ym == period and key in sub_keys and a > p)
        return {"accrued": accrued, "paid": paid, "rest": accrued - paid,
                "unpaidLessons": sum(unpaid_lessons.values()), "unpaidSubs": unpaid_subs,
                "unpaidStudents": sum(1 for a, p in col.values() if a > p)}

    async def salaries(request: web.Request, user) -> web.Response:
        """Все педагоги за месяц: начислено и сколько уже выплачено (решение владельца 03.10.2026 —
        статус выплаты виден прямо в списке, а не только на экране «Выплатить зарплату»),
        рядом — сколько занятий и абонементов этого педагога родители не оплатили
        (`collection`, решение владельца 03.10.2026: число занятий, а не процент)."""
        period = request.query.get("ym") or current_period()
        paid_by = await _paid_by_teacher(period)
        ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
        sub_groups = await _sub_groups_by_teacher(period)
        marks_cache: dict[str, StudentMonthLessons] = {}
        out = []
        for t in sorted(await teacher_repo.get_all(), key=lambda x: x.name.lower()):
            lines = await salary_service.lines_for(t, period)
            accrued = sum(ln.amount for ln in lines)
            paid = _owner_paid(t.teacher_id, accrued, paid_by.get(t.teacher_id, 0))
            sg = sub_groups.get(t.teacher_id, set())
            col = teacher_collection(ledger, period, t.teacher_id, sg)
            unpaid = await _unpaid_lessons(period, t.teacher_id, col, marks_cache)
            out.append({"id": t.teacher_id, "name": t.name, "accrued": accrued, "paid": paid,
                        "status": "paid" if accrued and paid >= accrued else ("partial" if paid > 0 else "none"),
                        "lessons": sum(1 for ln in lines if ln.kind in ("lesson", "in_shift")),
                        "isOwner": t.teacher_id in settings.owner_teacher_id_set,
                        "directPay": t.teacher_id in settings.direct_pay_teacher_id_set,
                        "collection": _collection_totals(col, unpaid, sg, ledger, period)})
        # итог — сбор месяца целиком (как плитка сводки): абонемент группы, которую вели двое,
        # у каждого педагога виден, но здесь считается один раз
        pc = period_collection(ledger, period)
        return _json({"period": period, "teachers": out, "total": sum(x["accrued"] for x in out),
                      "paid": sum(x["paid"] for x in out),
                      "collection": {"accrued": pc.accrued, "paid": pc.paid, "rest": pc.rest,
                                     "unpaidLessons": sum(x["collection"]["unpaidLessons"] for x in out),
                                     "unpaidSubs": sum(x["collection"]["unpaidSubs"] for x in out)}})

    async def salary_collection(request: web.Request, user) -> web.Response:
        """Кто из учеников педагога не оплатил месяц: ученик → начислено / зачтено / остаток."""
        tid = request.match_info["tid"]
        period = request.query.get("ym") or current_period()
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
        sg = (await _sub_groups_by_teacher(period)).get(tid, set())
        col = teacher_collection(ledger, period, tid, sg)
        unpaid = await _unpaid_lessons(period, tid, col, {})
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        students = [{"id": sid, "name": names.get(sid, sid), "accrued": a, "paid": p, "rest": a - p,
                     "unpaidLessons": unpaid.get(sid, 0),
                     "status": "paid" if a <= p else "partial" if p else "unpaid"} for sid, (a, p) in col.items()]
        students.sort(key=lambda x: (-x["rest"], x["name"]))
        return _json({"teacherId": tid, "name": t.name, "period": period, "students": students,
                      **_collection_totals(col, unpaid, sg, ledger, period)})

    async def _described_lines(t, period: str) -> list[dict]:
        """Строки зарплаты с подписью: у занятия — группа (и сколько пришло) или ученики.

        SalaryService имён не знает и оставляет label пустым; без подписи админ видел
        только «дата · минуты · сумма» и не мог понять, с кем был урок.
        """
        lines = await salary_service.lines_for(t, period)
        by_id = {ls.lesson_id: ls for ls in await lesson_repo.get_by_teacher_and_period(t.teacher_id, period)}
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        out = []
        for ln in lines:
            d = _salary_line(ln)
            ls = by_id.get(ln.lesson_id or "")
            if ls is not None and not ln.label:
                if ls.type == LessonType.GROUP:
                    who = [names.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
                    d["label"] = groups.get(ls.group_id, "Группа")
                    d["type"], d["students"] = "group", who
                else:
                    who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
                    d["label"] = " + ".join(who) or "Занятие"
                    d["type"], d["students"] = "individual", who
            out.append(d)
        return out

    async def salary_teacher(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        period = request.query.get("ym") or current_period()
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        lines = await _described_lines(t, period)
        total = sum(ln["amount"] for ln in lines)
        paid = _owner_paid(tid, total, (await _paid_by_teacher(period)).get(tid, 0))
        ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
        sg = (await _sub_groups_by_teacher(period)).get(tid, set())
        col = teacher_collection(ledger, period, tid, sg)
        unpaid = await _unpaid_lessons(period, tid, col, {})
        return _json({"teacherId": tid, "name": t.name, "period": period, "paid": paid,
                      "lines": lines, "total": total, "isOwner": tid in settings.owner_teacher_id_set,
                      "collection": _collection_totals(col, unpaid, sg, ledger, period)})

    async def payouts(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        paid_by_teacher = await _paid_by_teacher(period)
        out = []
        for t in sorted(await teacher_repo.get_all(), key=lambda x: x.name.lower()):
            acc = await salary_service.total_for(t, period)
            if acc == 0 and t.teacher_id not in paid_by_teacher:
                continue
            paid = _owner_paid(t.teacher_id, acc, paid_by_teacher.get(t.teacher_id, 0))
            out.append({"id": t.teacher_id, "name": t.name, "accrued": acc, "paid": paid,
                        "status": "paid" if paid >= acc else ("partial" if paid > 0 else "none"),
                        "isOwner": t.teacher_id in settings.owner_teacher_id_set})
        return _json({"period": period, "teachers": out, "accrued": sum(x["accrued"] for x in out), "paid": sum(x["paid"] for x in out)})

    async def payout_teacher(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        period = request.query.get("ym") or current_period()
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        lines = await _described_lines(t, period)          # те же подписи, что на экране «Зарплаты»
        rows = await payout_repo.get_by_teacher_period(tid, period)
        overrides = await override_repo.get_for_teacher_period(tid, period)
        return _json({
            "teacherId": tid, "name": t.name, "period": period,
            "accrued": sum(ln["amount"] for ln in lines),
            "paid": _owner_paid(tid, sum(ln["amount"] for ln in lines), sum(p.amount for p in rows)),
            "payouts": [{"id": p.payout_id, "amount": p.amount, "date": p.paid_at[:10], "comment": p.comment} for p in rows],
            "lines": lines,
            "overrides": [{"id": o.override_id, "date": o.date, "minutes": o.minutes, "comment": o.comment} for o in overrides],
            "isOwner": tid in settings.owner_teacher_id_set,
        })

    async def payout_add(request: web.Request, user) -> web.Response:
        body = await _read_json(request)
        if body is None:
            return _json({"error": "bad_request"}, status=400)
        tid, period, amount = body.get("teacherId"), body.get("periodMonth"), body.get("amount")
        comment = (body.get("comment") or "").strip()
        if not isinstance(tid, str) or not isinstance(period, str) or len(period) != 7 or not isinstance(amount, int) or amount <= 0:
            return _json({"error": "bad_request"}, status=400)
        if await teacher_repo.get_by_id(tid) is None:
            return _json({"error": "not_found"}, status=404)
        p = await payout_repo.add(tid, period, amount, user.tg_id, comment=comment)
        logger.info("Mini App: выплата %s %s %d руб. (%s) — админ %s", tid, period, amount, comment, user.tg_id)
        return _json({"id": p.payout_id})

    async def override_add(request: web.Request, user) -> web.Response:
        body = await _read_json(request)
        if body is None:
            return _json({"error": "bad_request"}, status=400)
        tid, day, minutes = body.get("teacherId"), body.get("date"), body.get("minutes")
        comment = (body.get("comment") or "").strip()
        if not isinstance(tid, str) or not isinstance(day, str) or len(day) != 10 or not isinstance(minutes, int) or minutes < 0:
            return _json({"error": "bad_request"}, status=400)
        if await teacher_repo.get_by_id(tid) is None:
            return _json({"error": "not_found"}, status=404)
        o = await override_repo.add(tid, day, minutes, comment, user.tg_id)
        return _json({"id": o.override_id})

    async def override_delete(request: web.Request, user) -> web.Response:
        ok = await override_repo.delete(request.match_info["oid"])
        return _json({"ok": ok}, status=200 if ok else 404)

    # ── история оплат ────────────────────────────────────────────────────
    async def payhist_search(request: web.Request, user) -> web.Response:
        q = request.query.get("q", "").strip().lower()
        counts: dict[str, int] = {}
        for p in await payment_repo.get_all():
            if p.status == PaymentStatus.PAID:
                counts[p.student_id] = counts.get(p.student_id, 0) + 1
        out = [{"id": s.student_id, "name": s.name, "payments": counts.get(s.student_id, 0)}
               for s in sorted(await student_repo.get_all(), key=lambda x: x.name.lower()) if not q or q in s.name.lower()]
        return _json({"students": out})

    async def payhist_months(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        by_period: dict[str, list] = {}
        for p in await payment_repo.get_all():
            if p.student_id == sid:
                by_period.setdefault(p.period_month, []).append(p)
        months = []
        for period in sorted(by_period, reverse=True):
            items = by_period[period]
            paid = sum(p.total_amount for p in items if p.status == PaymentStatus.PAID)
            pending = sum(p.total_amount for p in items if p.status != PaymentStatus.PAID and p.total_amount > 0)
            months.append({"period": period, "paid": paid, "pending": pending})
        return _json({"student": {"id": sid, "name": student.name}, "months": months,
                      "totalPaid": sum(m["paid"] for m in months)})

    async def payhist_month(request: web.Request, user) -> web.Response:
        sid, period = request.match_info["sid"], request.match_info["ym"]
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        pays = await payment_repo.get_by_student_and_period(sid, period)
        row = lambda p: {"id": p.payment_id, "teacherId": p.teacher_id, "teacherName": p.teacher_name or p.teacher_id,  # noqa: E731
                         "amount": p.total_amount, "paidAt": p.paid_at, "method": p.payment_method or "",
                         "comment": p.comment or "", "byTgId": p.confirmed_by_tg_id}
        paid = sorted((p for p in pays if p.status == PaymentStatus.PAID), key=lambda p: (p.paid_at or "", p.teacher_name or ""))
        pending = [p for p in pays if p.status != PaymentStatus.PAID and p.total_amount > 0]
        return _json({"student": {"id": sid, "name": student.name}, "period": period,
                      "paid": [row(p) for p in paid], "pending": [row(p) for p in pending]})

    routes = [
        ("POST", "/debtors/remind", debtors_remind),
        ("GET", "/profit", profit), ("GET", "/profit/day", profit_day), ("GET", "/profit/teacher/{tid}", profit_teacher),
        ("GET", "/profit/subscription/{gid}", profit_subscription), ("GET", "/profit/breakdown", profit_breakdown),
        ("GET", "/profit/units", profit_units), ("GET", "/profit/unit", profit_unit),
        ("POST", "/finance", finance_add), ("DELETE", "/finance/{eid}", finance_delete),
        ("GET", "/salaries", salaries), ("GET", "/salaries/{tid}", salary_teacher),
        ("GET", "/salaries/{tid}/collection", salary_collection),
        ("GET", "/payouts", payouts), ("GET", "/payouts/{tid}", payout_teacher), ("POST", "/payouts", payout_add),
        ("POST", "/salary-overrides", override_add), ("DELETE", "/salary-overrides/{oid}", override_delete),
        ("GET", "/payhist", payhist_search), ("GET", "/payhist/{sid}", payhist_months), ("GET", "/payhist/{sid}/{ym}", payhist_month),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, prefix + path, guard(handler))
    logger.info("Admin API (финансы): %d маршрутов", len(routes))
