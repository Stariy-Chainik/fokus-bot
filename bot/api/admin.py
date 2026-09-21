"""HTTP API кабинета администратора Telegram Mini App — /api/admin/*.

Тонкий слой над сервисами бота: суммы, остатки и отметки оплат считает тот же
PaymentService, что и Telegram-экраны, поэтому Mini App и бот всегда сходятся.
Авторизация — `Authorization: tma <initData>` (подпись Telegram проверяется на
каждый запрос) и роль admin из листа users. Для локальной разработки без
Telegram — `MINIAPP_DEV_TG_ID` + заголовок `Authorization: dev` (на проде пусто).
"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from aiohttp import web

from bot.handlers.admin.bills.helpers import _send_bill_to_parents, _student_group_names
from bot.services import payment_ledger
from bot.services.payment_methods import ADMIN_MANUAL
from bot.services.rosters import group_members
from bot.utils.dates import current_period, last_periods
from bot.utils.locks import InProgressGuard
from bot.utils.telegram_auth import verify_init_data
from config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "/api/admin"
_confirming = InProgressGuard()


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def auth_tg_id(request: web.Request) -> int | None:
    """tg_id из проверенного initData; в dev-режиме — из настроек по заголовку `dev`."""
    header = request.headers.get("Authorization", "")
    if header.startswith("tma "):
        parsed = verify_init_data(header[4:], settings.bot_token)
        if not parsed or not isinstance(parsed.get("user"), dict):
            return None
        tg_id = parsed["user"].get("id")
        return tg_id if isinstance(tg_id, int) else None
    if header == "dev" and settings.miniapp_dev_tg_id:
        return settings.miniapp_dev_tg_id
    return None


def _prev_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _bill_summary(bills: dict, payments: list) -> dict:
    """Итоги счёта месяца: начислено / оплачено / остаток (read-only, без синхронизации строк)."""
    paid_map = payment_ledger.paid_sums(payments)
    total = sum(agg.total for agg in bills.values())
    paid = sum(min(paid_map.get(key, 0), agg.total) for key, agg in bills.items())
    rest = sum(max(agg.total - paid_map.get(key, 0), 0) for key, agg in bills.items())
    return {"total": total, "paid": paid, "rest": rest}


def _mode(group) -> str:
    return getattr(group.billing_mode, "value", str(group.billing_mode))


def register_admin_api(app: web.Application, dp, bot=None) -> None:
    user_repo = dp["user_repo"]
    student_repo = dp["student_repo"]
    teacher_repo = dp["teacher_repo"]
    group_repo = dp["group_repo"]
    branch_repo = dp["branch_repo"]
    student_group_repo = dp["student_group_repo"]
    lesson_repo = dp["lesson_repo"]
    payment_repo = dp["payment_repo"]
    submission_repo = dp["submission_repo"]
    payment_service = dp["payment_service"]
    student_service = dp["student_service"]
    salary_service = dp["salary_service"]
    client_repo = dp["client_repo"]

    def admin_only(handler):
        async def wrapped(request: web.Request) -> web.Response:
            tg_id = auth_tg_id(request)
            if tg_id is None:
                return _json({"error": "unauthorized"}, status=401)
            user = await user_repo.get_by_tg_id(tg_id)
            if user is None or not user.is_admin:
                return _json({"error": "forbidden"}, status=403)
            try:
                return await handler(request, user)
            except web.HTTPException:
                raise
            except Exception as exc:
                logger.exception("Admin API %s: %s", request.path, exc)
                return _json({"error": "internal"}, status=500)
        return wrapped

    async def _groups_by_id() -> dict:
        return {g.group_id: g for g in await group_repo.get_all(include_archived=True)}

    async def _student_bill(student_id: str, period: str) -> tuple[dict, dict]:
        bills = await payment_service.compute_bills_for_student_period(student_id, period)
        payments = await payment_repo.get_by_student_and_period(student_id, period)
        return bills, _bill_summary(bills, payments)

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def me(request: web.Request, user) -> web.Response:
        # имя — из карточки педагога, если админ ведёт занятия (для приветствия в кабинете)
        teacher = await teacher_repo.get_by_id(user.teacher_id) if user.teacher_id else None
        return _json({"tgId": user.tg_id, "isAdmin": user.is_admin, "teacherId": user.teacher_id,
                      "name": teacher.name if teacher else ""})

    async def home(request: web.Request, user) -> web.Response:
        period = current_period()
        prev = _prev_period(period)
        debt_map = await payment_service.compute_debt_map(since_period=settings.debtors_since_period or None)
        pending = sum(m.get(period, 0) for m in debt_map.values())
        debtors = sum(1 for m in debt_map.values() if any(ym < period and amt > 0 for ym, amt in m.items()))
        today = date.today().isoformat()
        lessons_today = [ls for ls in await lesson_repo.get_all() if ls.date == today]
        return _json({
            "today": today, "period": period, "prevPeriod": prev,
            "pendingTotal": pending, "debtorsCount": debtors, "lessonsToday": len(lessons_today),
            "studentsCount": len(await student_repo.get_all()),
        })

    # ── ученики ──────────────────────────────────────────────────────────
    async def students(request: web.Request, user) -> web.Response:
        """Список с фильтрами: q — по имени, branch — id филиала, group — id группы,
        noparent=1 — без родителя в боте, debt=1 — с долгом за закрытые месяцы (как в «Должниках»)."""
        q = request.query.get("q", "").strip().lower()
        branch_id = request.query.get("branch", "").strip()
        group_id = request.query.get("group", "").strip()
        no_parent = request.query.get("noparent") == "1"
        with_debt = request.query.get("debt") == "1"
        groups = await _groups_by_id()
        by_student = await student_group_repo.get_map_by_student()
        debtors: set[str] = set()
        if with_debt:
            period = current_period()
            debt_map = await payment_service.compute_debt_map(since_period=settings.debtors_since_period or None)
            debtors = {sid for sid, m in debt_map.items() if any(ym < period and amt > 0 for ym, amt in m.items())}
        out = []
        for s in sorted(await student_repo.get_all(), key=lambda x: x.name.lower()):
            gids = by_student.get(s.student_id, [])
            in_branch = not branch_id or any(g in groups and groups[g].branch_id == branch_id for g in gids)
            if (q and q not in s.name.lower()) or (group_id and group_id not in gids) or not in_branch \
                    or (no_parent and s.parent_addrs) or (with_debt and s.student_id not in debtors):
                continue
            out.append({
                "id": s.student_id, "name": s.name,
                "groups": [groups[g].name for g in gids if g in groups],
                "hasParent": bool(s.parent_addrs), "isAthlete": bool(s.athlete_tg_id),
            })
        return _json({"students": out, "total": len(await student_repo.get_all())})

    async def student_card(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        card = await student_service.get_student_card(sid)
        if card is None:
            return _json({"error": "not_found"}, status=404)
        period = current_period()
        months = []
        for ym in last_periods(3):
            _, summary = await _student_bill(sid, ym)
            months.append({"period": ym, **summary})
        debt = sum(m["rest"] for m in months if m["period"] < period)
        s = card.student
        return _json({
            "id": s.student_id, "name": s.name, "tier": getattr(s.group_tier, "value", str(s.group_tier)),
            "partner": {"id": card.partner.student_id, "name": card.partner.name} if card.partner else None,
            "client": {"id": card.client.client_id, "name": card.client.name, "phone": card.client.phone} if card.client else None,
            "parents": [{"platform": a[0], "id": a[1]} for a in s.parent_addrs],
            "isAthlete": bool(s.athlete_tg_id),
            "teachers": card.teacher_names,
            "groups": [{"id": g.group_id, "name": g.group.name if g.group else g.group_id,
                        "branch": g.branch_name, "mode": _mode(g.group) if g.group else None} for g in card.groups],
            "months": months, "debt": debt,
        })

    # ── педагоги ─────────────────────────────────────────────────────────
    async def teachers(request: web.Request, user) -> web.Response:
        prev = _prev_period(current_period())
        groups = await _groups_by_id()
        tg_rows = await dp["teacher_group_repo"].get_all()
        out = []
        for t in sorted(await teacher_repo.get_all(), key=lambda x: x.name.lower()):
            submitted = await submission_repo.get_by_teacher_and_period(t.teacher_id, prev)
            gids = [r.group_id for r in tg_rows if r.teacher_id == t.teacher_id]
            out.append({
                "id": t.teacher_id, "name": t.name,
                "groups": [groups[g].name for g in gids if g in groups],
                "submittedPrev": submitted is not None,
                "isOwner": t.teacher_id in settings.owner_teacher_id_set,
                "directPay": t.teacher_id in settings.direct_pay_teacher_id_set,
            })
        return _json({"teachers": out, "prevPeriod": prev})

    async def teacher_card(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        period = current_period()
        groups = await _groups_by_id()
        gids = [r.group_id for r in await dp["teacher_group_repo"].get_all() if r.teacher_id == tid]
        subs = sorted({s.period_month for s in await submission_repo.get_by_teacher(tid)}, reverse=True)
        return _json({
            "id": t.teacher_id, "name": t.name, "tgId": t.tg_id,
            "rates": {"group": t.rate_group, "teacher": t.rate_for_teacher, "student": t.rate_for_student},
            "groups": [{"id": g, "name": groups[g].name} for g in gids if g in groups],
            "submitted": subs, "period": period,
            "salary": await salary_service.total_for(t, period),
            "isOwner": tid in settings.owner_teacher_id_set,
            "directPay": tid in settings.direct_pay_teacher_id_set,
        })

    # ── подтверждение оплаты ─────────────────────────────────────────────
    async def pay_groups(request: web.Request, user) -> web.Response:
        branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
        groups = await group_repo.get_all()
        counts: dict[str, int] = {}
        for gids in (await student_group_repo.get_map_by_student()).values():
            for gid in gids:
                counts[gid] = counts.get(gid, 0) + 1
        out = []
        for b in branches:
            out.append({"id": b.branch_id, "name": b.name, "groups": [
                {"id": g.group_id, "name": g.name, "mode": _mode(g), "price": g.price_full,
                 "students": counts.get(g.group_id, 0)}
                for g in sorted(groups, key=lambda g: (g.sort_order, g.name)) if g.branch_id == b.branch_id
            ]})
        return _json({"branches": out})

    async def pay_students(request: web.Request, user) -> web.Response:
        period = request.query.get("ym") or current_period()
        group_id = request.query.get("group") or ""
        if group_id:
            members = await group_members(student_repo, student_group_repo, group_id)
        else:
            members = sorted(await student_repo.get_all(), key=lambda s: s.name.lower())
        out = []
        for s in members:
            _, summary = await _student_bill(s.student_id, period)
            out.append({"id": s.student_id, "name": s.name, **summary})
        return _json({"period": period, "students": out})

    async def pay_student(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        ledgers = await payment_service.ledger_for(student, period)
        positions = []
        for key, ledger in ledgers.items():
            positions.append({
                "key": key, "name": ledger.name, "group": ledger.group, "subscription": ledger.subscription,
                "accrued": ledger.accrued, "paid": ledger.paid, "remainder": ledger.remainder,
                "paidRows": [{"id": p.payment_id, "amount": p.total_amount, "date": (p.paid_at or "")[:10],
                              "method": p.payment_method or ""} for p in ledger.paid_rows],
                "pending": {"id": ledger.pending.payment_id, "amount": ledger.pending.total_amount} if ledger.pending else None,
                "unpaidLessons": sum(1 for m in payment_ledger.lesson_marks(ledger.items, ledger.paid) if not m["paid"]),
            })
        return _json({"student": {"id": sid, "name": student.name}, "period": period, "positions": positions})

    async def pay_marks(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        key = request.query.get("key", "")
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        marks, ledger = await payment_service.teacher_lesson_marks(student, period, key)
        if ledger is None:
            return _json({"error": "not_found"}, status=404)
        return _json({
            "student": {"id": sid, "name": student.name}, "period": period,
            "ledger": {"key": key, "name": ledger.name, "group": ledger.group,
                       "accrued": ledger.accrued, "paid": ledger.paid, "remainder": ledger.remainder},
            "marks": [{"lessonId": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                       "amount": m["amount"], "paid": m["paid"], "lessonType": m.get("lesson_type")} for m in marks],
        })

    async def pay_confirm(request: web.Request, user) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        sid, period, key = body.get("studentId"), body.get("periodMonth"), body.get("key")
        amount = body.get("amount")
        method = body.get("method") or ADMIN_MANUAL
        lessons = [x for x in (body.get("lessonIds") or []) if isinstance(x, str)]
        if not all(isinstance(x, str) and x for x in (sid, period, key)) or not isinstance(amount, int) or amount <= 0:
            return _json({"error": "bad_request"}, status=400)
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        guard_key = f"{sid}:{period}:{key}"
        if guard_key in _confirming:
            return _json({"error": "in_progress"}, status=409)
        _confirming.add(guard_key)
        try:
            credited, rows = await payment_service.record_payment(
                sid, student.name, period, amount, user.tg_id, [key], "отмечено вручную", method,
                lesson_ids=lessons,
            )
        finally:
            _confirming.discard(guard_key)
        logger.info("Mini App: админ %s отметил оплату %d руб.: %s %s %s", user.tg_id, credited, sid, period, key)
        return _json({"credited": credited, "rows": rows})

    async def pay_confirm_invoice(request: web.Request, user) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        payment_id = body.get("paymentId")
        method = body.get("method") or ADMIN_MANUAL
        if not isinstance(payment_id, str) or not payment_id:
            return _json({"error": "bad_request"}, status=400)
        if payment_id in _confirming:
            return _json({"error": "in_progress"}, status=409)
        _confirming.add(payment_id)
        try:
            ok = await payment_service.confirm_payment(payment_id, user.tg_id, method)
        finally:
            _confirming.discard(payment_id)
        return _json({"ok": ok})

    # ── счёт ученика ─────────────────────────────────────────────────────
    async def bill(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        bills, summary = await _student_bill(sid, period)
        paid_map = payment_ledger.paid_sums(await payment_repo.get_by_student_and_period(sid, period))
        rows = []
        for key, agg in bills.items():
            paid = paid_map.get(key, 0)
            rows.append({
                "key": key, "name": agg.name, "group": getattr(agg, "group", False), "subscription": agg.subscription,
                "total": agg.total, "paid": min(paid, agg.total), "rest": max(agg.total - paid, 0),
                "items": [{"date": m["date"], "durationMin": m["duration_min"], "amount": m["amount"], "paid": m["paid"]}
                          for m in payment_ledger.lesson_marks(agg.items, paid)],
            })
        return _json({
            "student": {"id": sid, "name": student.name}, "period": period,
            "groups": await _student_group_names(sid, student_group_repo, group_repo),
            "rows": rows, **summary,
        })

    async def bill_send(request: web.Request, user) -> web.Response:
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        if not bills:
            return _json({"error": "nothing_to_send"}, status=409)
        group_names = await _student_group_names(sid, student_group_repo, group_repo)
        recipients, sent_to, _ = await _send_bill_to_parents(
            cast(Any, SimpleNamespace(bot=bot)), student, period, bills, group_names, payment_service, client_repo,
        )
        return _json({"recipients": recipients, "sentTo": sent_to})

    # ── должники ─────────────────────────────────────────────────────────
    async def debtors(request: web.Request, user) -> web.Response:
        period = current_period()
        debt_map = await payment_service.compute_debt_map(since_period=settings.debtors_since_period or None)
        students_by_id = {s.student_id: s for s in await student_repo.get_all()}
        out = []
        for sid, months in debt_map.items():
            s = students_by_id.get(sid)
            if s is None or not months:
                continue
            closed = sum(a for ym, a in months.items() if ym < period)
            out.append({"id": sid, "name": s.name, "months": dict(sorted(months.items())),
                        "closedTotal": closed, "currentTotal": months.get(period, 0), "hasParent": bool(s.parent_addrs)})
        out.sort(key=lambda r: (-r["closedTotal"], -r["currentTotal"], r["name"]))
        return _json({"period": period, "debtors": out})

    routes = [
        ("GET", "/me", me), ("GET", "/home", home),
        ("GET", "/students", students), ("GET", "/students/{sid}", student_card),
        ("GET", "/teachers", teachers), ("GET", "/teachers/{tid}", teacher_card),
        ("GET", "/pay/groups", pay_groups), ("GET", "/pay/students", pay_students),
        ("GET", "/pay/student/{sid}", pay_student), ("GET", "/pay/marks/{sid}", pay_marks),
        ("POST", "/pay/confirm", pay_confirm), ("POST", "/pay/confirm-invoice", pay_confirm_invoice),
        ("GET", "/bill/{sid}", bill), ("POST", "/bill/{sid}/send", bill_send),
        ("GET", "/debtors", debtors),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, admin_only(handler))
    from bot.api.admin_finance import register_finance_routes
    from bot.api.admin_lessons import register_lesson_routes
    from bot.api.admin_manage import register_manage_routes
    register_finance_routes(app, dp, admin_only, PREFIX)
    register_lesson_routes(app, dp, admin_only, PREFIX)
    register_manage_routes(app, dp, admin_only, PREFIX)
    from bot.api.admin_inbox import register_inbox_routes
    register_inbox_routes(app, dp, admin_only, PREFIX, bot)
    logger.info("Admin API зарегистрирован: %d маршрутов под %s", len(routes), PREFIX)
