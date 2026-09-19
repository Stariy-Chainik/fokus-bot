"""Кабинет администратора, этап 2: должники (напоминание), прибыль, зарплаты, выплаты, история оплат.

Маршруты регистрируются из bot/api/admin.py под тем же префиксом и той же проверкой роли.
"""
from __future__ import annotations

import logging
from datetime import date

from aiohttp import web

from bot.models.enums import LessonType, PaymentStatus
from bot.services.payment_service import build_debtor_rows
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
        return _json({"sent": sent, "failed": failed, "skipped": skipped,
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
    async def salaries(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        out = []
        for t in sorted(await teacher_repo.get_all(), key=lambda x: x.name.lower()):
            lines = await salary_service.lines_for(t, period)
            out.append({"id": t.teacher_id, "name": t.name, "accrued": sum(ln.amount for ln in lines),
                        "lessons": sum(1 for ln in lines if ln.kind in ("lesson", "in_shift")),
                        "isOwner": t.teacher_id in settings.owner_teacher_id_set,
                        "directPay": t.teacher_id in settings.direct_pay_teacher_id_set})
        return _json({"period": period, "teachers": out, "total": sum(x["accrued"] for x in out)})

    async def salary_teacher(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        period = request.query.get("ym") or current_period()
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        lines = await salary_service.lines_for(t, period)
        return _json({"teacherId": tid, "name": t.name, "period": period,
                      "lines": [_salary_line(ln) for ln in lines], "total": sum(ln.amount for ln in lines)})

    async def payouts(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        paid_by_teacher: dict[str, int] = {}
        for p in await payout_repo.get_by_period(period):
            paid_by_teacher[p.teacher_id] = paid_by_teacher.get(p.teacher_id, 0) + p.amount
        out = []
        for t in sorted(await teacher_repo.get_all(), key=lambda x: x.name.lower()):
            acc = await salary_service.total_for(t, period)
            if acc == 0 and t.teacher_id not in paid_by_teacher:
                continue
            paid = paid_by_teacher.get(t.teacher_id, 0)
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
        lines = await salary_service.lines_for(t, period)
        rows = await payout_repo.get_by_teacher_period(tid, period)
        overrides = await override_repo.get_for_teacher_period(tid, period)
        return _json({
            "teacherId": tid, "name": t.name, "period": period,
            "accrued": sum(ln.amount for ln in lines), "paid": sum(p.amount for p in rows),
            "payouts": [{"id": p.payout_id, "amount": p.amount, "date": p.paid_at[:10], "comment": p.comment} for p in rows],
            "lines": [_salary_line(ln) for ln in lines],
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
        ("POST", "/finance", finance_add), ("DELETE", "/finance/{eid}", finance_delete),
        ("GET", "/salaries", salaries), ("GET", "/salaries/{tid}", salary_teacher),
        ("GET", "/payouts", payouts), ("GET", "/payouts/{tid}", payout_teacher), ("POST", "/payouts", payout_add),
        ("POST", "/salary-overrides", override_add), ("DELETE", "/salary-overrides/{oid}", override_delete),
        ("GET", "/payhist", payhist_search), ("GET", "/payhist/{sid}", payhist_months), ("GET", "/payhist/{sid}/{ym}", payhist_month),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, prefix + path, guard(handler))
    logger.info("Admin API (финансы): %d маршрутов", len(routes))
