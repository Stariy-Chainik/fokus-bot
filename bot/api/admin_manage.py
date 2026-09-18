"""Кабинет администратора, этап 3: филиалы и группы, биллинг, состав, карточки учеников и педагогов.

Все записи идут через те же репозитории и сервисы, что и Telegram-хендлеры
(admin/branches/*, admin/students/*, admin/teachers/*), поэтому правила совпадают:
смена цены абонемента — «только вперёд» с фиксацией прошлых месяцев, выход из
абонементной группы — пометкой месяца ухода, партнёры — симметрично.
"""
from __future__ import annotations

import logging

from aiohttp import web

from bot.models.enums import GroupBillingMode, StudentGroupTier
from bot.services.membership import leave_group
from bot.services.student_service import TierToggleError
from bot.utils.dates import current_period

logger = logging.getLogger(__name__)

_TIER_ERRORS = {
    TierToggleError.STUDENT_NOT_FOUND: "not_found",
    TierToggleError.NO_GROUPS: "no_groups",
    TierToggleError.NO_PER_VISIT_GROUP: "no_per_visit_group",
}


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> dict | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _is_period(value) -> bool:
    return isinstance(value, str) and len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:].isdigit()


def _amount(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _mode(group) -> str:
    return getattr(group.billing_mode, "value", str(group.billing_mode))


def _group_dto(g, students: int = 0, teachers: list[str] | None = None) -> dict:
    return {
        "id": g.group_id, "name": g.name, "branchId": g.branch_id, "mode": _mode(g), "archived": bool(g.archived),
        "priceFull": g.price_full, "priceShort": g.price_short, "durationFull": g.duration_full, "durationShort": g.duration_short,
        "students": students, "teachers": teachers or [],
    }


def register_manage_routes(app: web.Application, dp, guard, prefix: str) -> None:
    branch_repo = dp["branch_repo"]
    group_repo = dp["group_repo"]
    student_group_repo = dp["student_group_repo"]
    teacher_group_repo = dp["teacher_group_repo"]
    override_repo = dp["subscription_override_repo"]
    teacher_repo = dp["teacher_repo"]
    student_repo = dp["student_repo"]
    client_repo = dp["client_repo"]
    user_repo = dp["user_repo"]
    submission_repo = dp["submission_repo"]
    payment_service = dp["payment_service"]
    student_service = dp["student_service"]

    # ── филиалы ──────────────────────────────────────────────────────────
    async def branches(request: web.Request, user) -> web.Response:
        groups = await group_repo.get_all(include_archived=True)
        members = await student_group_repo.get_all()
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        tg_rows = await teacher_group_repo.get_all()
        out = []
        for b in sorted(await branch_repo.get_all(), key=lambda x: x.name):
            gs = sorted((g for g in groups if g.branch_id == b.branch_id), key=lambda g: (g.archived, g.sort_order, g.name))
            out.append({"id": b.branch_id, "name": b.name, "groups": [
                _group_dto(g, sum(1 for m in members if m.group_id == g.group_id and not m.left_period),
                           [teachers[r.teacher_id] for r in tg_rows if r.group_id == g.group_id and r.teacher_id in teachers])
                for g in gs
            ]})
        return _json({"branches": out})

    async def branch_add(request: web.Request, user) -> web.Response:
        body = await _body(request)
        name = (body or {}).get("name", "").strip() if body else ""
        if not name:
            return _json({"error": "bad_request"}, status=400)
        b = await branch_repo.add(name)
        return _json({"id": b.branch_id})

    async def branch_rename(request: web.Request, user) -> web.Response:
        body = await _body(request)
        name = (body or {}).get("name", "").strip() if body else ""
        if not name:
            return _json({"error": "bad_request"}, status=400)
        ok = await branch_repo.update_name(request.match_info["bid"], name)
        return _json({"ok": ok}, status=200 if ok else 404)

    async def branch_delete(request: web.Request, user) -> web.Response:
        bid = request.match_info["bid"]
        if await group_repo.get_by_branch(bid, include_archived=True):
            return _json({"error": "has_groups"}, status=409)
        ok = await branch_repo.delete(bid)
        return _json({"ok": ok}, status=200 if ok else 404)

    # ── группы ───────────────────────────────────────────────────────────
    async def group_card(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        g = await group_repo.get_by_id(gid)
        if g is None:
            return _json({"error": "not_found"}, status=404)
        branch = await branch_repo.get_by_id(g.branch_id)
        assigned = set(await teacher_group_repo.get_teachers_for_group(gid))
        students = {s.student_id: s for s in await student_repo.get_all()}
        membership = await student_group_repo.get_membership_map()
        members = []
        for (sid, mgid), m in membership.items():
            if mgid != gid or sid not in students:
                continue
            s = students[sid]
            members.append({"id": sid, "name": s.name, "joined": m.joined_period or "", "left": m.left_period or "",
                            "tier": getattr(s.group_tier, "value", str(s.group_tier)), "hasParent": bool(s.parent_addrs)})
        members.sort(key=lambda x: (bool(x["left"]), x["name"].lower()))
        overrides = [{"periodMonth": o.period_month, "studentId": o.student_id,
                      "studentName": students[o.student_id].name if o.student_id in students else None, "amount": o.amount}
                     for o in await override_repo.get_for_group(gid)]
        overrides.sort(key=lambda o: (o["periodMonth"] != "*", o["periodMonth"], o["studentName"] or ""))
        return _json({
            **_group_dto(g, sum(1 for m in members if not m["left"])), "branchName": branch.name if branch else g.branch_id,
            "teachers": [{"id": t.teacher_id, "name": t.name, "assigned": t.teacher_id in assigned}
                         for t in sorted(await teacher_repo.get_all(), key=lambda t: t.name)],
            "members": members, "overrides": overrides,
        })

    async def group_add(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        name, bid = (body.get("name") or "").strip(), body.get("branchId")
        if not name or not isinstance(bid, str) or await branch_repo.get_by_id(bid) is None:
            return _json({"error": "bad_request"}, status=400)
        g = await group_repo.add(bid, name)
        return _json({"id": g.group_id})

    async def group_patch(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        body = await _body(request) or {}
        if await group_repo.get_by_id(gid) is None:
            return _json({"error": "not_found"}, status=404)
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                return _json({"error": "bad_request"}, status=400)
            await group_repo.update_name(gid, name)
        if "archived" in body:
            await group_repo.set_archived(gid, bool(body["archived"]))
        return _json({"ok": True})

    async def group_delete(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        if await group_repo.get_by_id(gid) is None:
            return _json({"error": "not_found"}, status=404)
        await student_group_repo.remove_all_for_group(gid)
        await teacher_group_repo.remove_all_for_group(gid)
        ok = await group_repo.delete(gid)
        logger.info("Mini App: админ %s удалил группу %s", user.tg_id, gid)
        return _json({"ok": ok})

    async def group_billing(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        g = await group_repo.get_by_id(gid)
        if g is None:
            return _json({"error": "not_found"}, status=404)
        body = await _body(request) or {}
        mode = body.get("mode")
        if mode not in ("none", "per_visit", "subscription"):
            return _json({"error": "bad_request"}, status=400)
        price_full = _amount(body.get("priceFull", g.price_full))
        price_short = _amount(body.get("priceShort", g.price_short))
        dur_full = _amount(body.get("durationFull", g.duration_full)) or g.duration_full
        dur_short = _amount(body.get("durationShort", g.duration_short)) or g.duration_short
        if price_full is None or price_short is None:
            return _json({"error": "bad_request"}, status=400)
        pinned = 0
        if mode == "subscription":
            eff = body.get("effectivePeriod")
            if not price_full or not _is_period(eff):
                return _json({"error": "bad_request"}, status=400)
            # как в боте: прошлое фиксируем старой ценой при смене, нулём — при первом включении
            pin_amount = g.price_full if g.billing_mode == GroupBillingMode.SUBSCRIPTION else 0
            pinned = await payment_service.pin_subscription_history(gid, eff, pin_amount)
            await group_repo.update_billing(gid, GroupBillingMode.SUBSCRIPTION, g.price_short, g.duration_short,
                                            price_full, g.duration_full)
        elif mode == "per_visit":
            if not price_full:
                return _json({"error": "bad_request"}, status=400)
            await group_repo.update_billing(gid, GroupBillingMode.PER_VISIT, price_short, dur_short, price_full, dur_full)
        else:
            await group_repo.update_billing(gid, GroupBillingMode.NONE, g.price_short, g.duration_short, g.price_full, g.duration_full)
        logger.info("Mini App: админ %s — биллинг группы %s: %s %s", user.tg_id, gid, mode, body)
        return _json({"ok": True, "pinned": pinned})

    async def override_put(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        if await group_repo.get_by_id(gid) is None:
            return _json({"error": "not_found"}, status=404)
        body = await _body(request) or {}
        period, sid, amount = body.get("periodMonth"), body.get("studentId") or None, _amount(body.get("amount"))
        if amount is None or not (period == "*" and sid or _is_period(period)):
            return _json({"error": "bad_request"}, status=400)
        if sid and await student_repo.get_by_id(sid) is None:
            return _json({"error": "not_found"}, status=404)
        await override_repo.upsert(gid, period, sid, amount)
        return _json({"ok": True})

    async def override_delete(request: web.Request, user) -> web.Response:
        gid = request.match_info["gid"]
        body = await _body(request) or {}
        period, sid = body.get("periodMonth"), body.get("studentId") or None
        if not isinstance(period, str):
            return _json({"error": "bad_request"}, status=400)
        ok = await override_repo.delete(gid, period, sid)
        return _json({"ok": ok}, status=200 if ok else 404)

    async def _assign_teacher(tid: str, gid: str, assigned: bool) -> web.Response:
        if await group_repo.get_by_id(gid) is None or await teacher_repo.get_by_id(tid) is None:
            return _json({"error": "not_found"}, status=404)
        exists = await teacher_group_repo.exists(tid, gid)
        if assigned and not exists:
            await teacher_group_repo.add(tid, gid)
        elif not assigned and exists:
            await teacher_group_repo.remove(tid, gid)
        return _json({"ok": True, "assigned": assigned})

    async def group_teachers(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        tid = body.get("teacherId")
        if not isinstance(tid, str) or not isinstance(body.get("assigned"), bool):
            return _json({"error": "bad_request"}, status=400)
        return await _assign_teacher(tid, request.match_info["gid"], body["assigned"])

    async def _join(sid: str, gid: str, joined: str | None) -> web.Response:
        if await group_repo.get_by_id(gid) is None or await student_repo.get_by_id(sid) is None:
            return _json({"error": "not_found"}, status=404)
        await student_group_repo.add(sid, gid, joined if _is_period(joined) else None)
        return _json({"ok": True})

    async def _leave(sid: str, gid: str, left: str | None) -> web.Response:
        if await group_repo.get_by_id(gid) is None or await student_repo.get_by_id(sid) is None:
            return _json({"error": "not_found"}, status=404)
        left_period = left if isinstance(left, str) and _is_period(left) else current_period()
        result = await leave_group(sid, gid, left_period, group_repo, student_group_repo)
        return _json({"ok": result != "missing", "result": result}, status=200 if result != "missing" else 404)

    async def member_add(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        sid = body.get("studentId")
        if not isinstance(sid, str):
            return _json({"error": "bad_request"}, status=400)
        return await _join(sid, request.match_info["gid"], body.get("joinedPeriod"))

    async def member_remove(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        return await _leave(request.match_info["sid"], request.match_info["gid"], body.get("leftPeriod"))

    async def member_periods(request: web.Request, user) -> web.Response:
        gid, sid = request.match_info["gid"], request.match_info["sid"]
        body = await _body(request) or {}
        if not await student_group_repo.exists(sid, gid):
            return _json({"error": "not_found"}, status=404)
        joined, left = body.get("joinedPeriod"), body.get("leftPeriod")
        if joined is not None:
            if not _is_period(joined):
                return _json({"error": "bad_request"}, status=400)
            await student_group_repo.set_joined_period(sid, gid, joined)
        if left is not None:
            if left != "" and not _is_period(left):
                return _json({"error": "bad_request"}, status=400)
            await student_group_repo.set_left_period(sid, gid, left)
        return _json({"ok": True})

    # ── ученики ──────────────────────────────────────────────────────────
    async def student_add(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        name = (body.get("name") or "").strip()
        gids = body.get("groupIds") or []
        if not name or not isinstance(gids, list):
            return _json({"error": "bad_request"}, status=400)
        groups = {g.group_id for g in await group_repo.get_all(include_archived=True)}
        if any(g not in groups for g in gids):
            return _json({"error": "bad_request"}, status=400)
        s = await student_repo.add(name)
        for gid in gids:
            await student_group_repo.add(s.student_id, gid)
        logger.info("Mini App: админ %s создал ученика %s (%s), группы %s", user.tg_id, s.student_id, name, gids)
        return _json({"id": s.student_id})

    async def student_patch(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return _json({"error": "bad_request"}, status=400)
        ok = await student_repo.update_name(request.match_info["sid"], name)
        return _json({"ok": ok}, status=200 if ok else 404)

    async def student_delete(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        ok = await student_service.delete_student(sid)
        if ok:
            logger.info("Mini App: админ %s удалил ученика %s", user.tg_id, sid)
        return _json({"ok": ok}, status=200 if ok else 404)

    async def partner_candidates(request: web.Request, user) -> web.Response:
        s = await student_repo.get_by_id(request.match_info["sid"])
        if s is None:
            return _json({"error": "not_found"}, status=404)
        cands = await student_service.partner_candidates(s)
        return _json({"noGroups": cands is None, "partnerId": s.partner_id,
                      "candidates": [{"id": c.student_id, "name": c.name, "hasPartner": busy} for c, busy in (cands or [])]})

    async def partner_put(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        body = await _body(request) or {}
        pid = body.get("partnerId") or None
        if await student_repo.get_by_id(sid) is None or (pid and await student_repo.get_by_id(pid) is None):
            return _json({"error": "not_found"}, status=404)
        if pid == sid:
            return _json({"error": "bad_request"}, status=400)
        if pid:
            await student_repo.set_partner(sid, pid)
        else:
            await student_repo.clear_partner(sid)
        return _json({"ok": True})

    async def student_groups(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        body = await _body(request) or {}
        gid = body.get("groupId")
        if not isinstance(gid, str) or not isinstance(body.get("member"), bool):
            return _json({"error": "bad_request"}, status=400)
        return await _join(sid, gid, body.get("joinedPeriod")) if body["member"] else await _leave(sid, gid, body.get("leftPeriod"))

    async def student_tier(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        err = await student_service.toggle_tier(sid)
        if err is not None:
            code = _TIER_ERRORS.get(err, "bad_request")
            return _json({"error": code}, status=404 if code == "not_found" else 409)
        s = await student_repo.get_by_id(sid)
        return _json({"ok": True, "tier": getattr(s.group_tier, "value", str(s.group_tier)) if s else StudentGroupTier.FULL.value})

    async def clients(request: web.Request, user) -> web.Response:
        q = request.query.get("q", "").strip().lower()
        students = await student_repo.get_all()
        out = []
        for c in sorted(await client_repo.get_all(), key=lambda c: c.name.lower()):
            if q and q not in c.name.lower() and q not in (c.phone or ""):
                continue
            out.append({"id": c.client_id, "name": c.name, "phone": c.phone or "", "tgId": c.tg_id,
                        "students": [s.name for s in students if s.client_id == c.client_id]})
        return _json({"clients": out})

    async def student_client(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        body = await _body(request) or {}
        cid = body.get("clientId")
        if isinstance(cid, str) and cid:
            if await client_repo.get_by_id(cid) is None:
                return _json({"error": "not_found"}, status=404)
        else:
            name = (body.get("name") or "").strip() or s.name
            phone = (body.get("phone") or "").strip()
            if not phone:
                return _json({"error": "bad_request"}, status=400)
            cid = (await client_repo.create(name, user.tg_id, phone)).client_id
        await student_repo.set_client_id(sid, cid)
        return _json({"ok": True, "clientId": cid})

    async def athlete_unlink(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        await student_repo.set_athlete_tg_id(sid, None)
        return _json({"ok": True})

    # ── педагоги ─────────────────────────────────────────────────────────
    def _rates(body: dict) -> tuple[int, int, int] | None:
        r = body.get("rates") or {}
        vals = tuple(_amount(r.get(k)) for k in ("group", "teacher", "student"))
        return None if any(v is None for v in vals) else vals  # type: ignore[return-value]

    async def teacher_add(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        name, rates, tg_id = (body.get("name") or "").strip(), _rates(body), body.get("tgId")
        if not name or rates is None or (tg_id is not None and not isinstance(tg_id, int)):
            return _json({"error": "bad_request"}, status=400)
        t = await teacher_repo.add(tg_id=tg_id, name=name, rate_group=rates[0], rate_for_teacher=rates[1], rate_for_student=rates[2])
        linked = False
        if t.tg_id:
            existing = await user_repo.get_by_tg_id(t.tg_id)
            if existing is None:
                await user_repo.add(tg_id=t.tg_id, teacher_id=t.teacher_id)
            else:
                await user_repo.update_teacher_id(t.tg_id, t.teacher_id)
            linked = True
        logger.info("Mini App: админ %s добавил педагога %s (%s)", user.tg_id, t.teacher_id, name)
        return _json({"id": t.teacher_id, "linked": linked})

    async def teacher_patch(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        body = await _body(request) or {}
        rates = _rates(body)
        if rates is None:
            return _json({"error": "bad_request"}, status=400)
        ok = await teacher_repo.update_rates(tid, *rates)
        if ok:
            logger.info("Mini App: админ %s изменил ставки %s → %s", user.tg_id, tid, rates)
        return _json({"ok": ok}, status=200 if ok else 404)

    async def teacher_delete(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        if await teacher_repo.get_by_id(tid) is None:
            return _json({"error": "not_found"}, status=404)
        await teacher_group_repo.remove_all_for_teacher(tid)
        ok = await teacher_repo.delete(tid)
        if ok:
            await user_repo.delete_by_teacher_id(tid)
            logger.info("Mini App: админ %s удалил педагога %s", user.tg_id, tid)
        return _json({"ok": ok})

    async def teacher_groups(request: web.Request, user) -> web.Response:
        body = await _body(request) or {}
        gid = body.get("groupId")
        if not isinstance(gid, str) or not isinstance(body.get("assigned"), bool):
            return _json({"error": "bad_request"}, status=400)
        return await _assign_teacher(request.match_info["tid"], gid, body["assigned"])

    async def teacher_open_period(request: web.Request, user) -> web.Response:
        tid, period = request.match_info["tid"], request.match_info["ym"]
        ok = await submission_repo.delete_by_teacher_and_period(tid, period)
        if ok:
            logger.info("Mini App: админ %s открыл период %s педагогу %s", user.tg_id, period, tid)
        return _json({"ok": ok}, status=200 if ok else 404)

    routes = [
        ("GET", "/branches", branches), ("POST", "/branches", branch_add),
        ("PATCH", "/branches/{bid}", branch_rename), ("DELETE", "/branches/{bid}", branch_delete),
        ("GET", "/groups/{gid}", group_card), ("POST", "/groups", group_add),
        ("PATCH", "/groups/{gid}", group_patch), ("DELETE", "/groups/{gid}", group_delete),
        ("PUT", "/groups/{gid}/billing", group_billing),
        ("PUT", "/groups/{gid}/overrides", override_put), ("DELETE", "/groups/{gid}/overrides", override_delete),
        ("PUT", "/groups/{gid}/teachers", group_teachers),
        ("POST", "/groups/{gid}/members", member_add), ("DELETE", "/groups/{gid}/members/{sid}", member_remove),
        ("PUT", "/groups/{gid}/members/{sid}", member_periods),
        ("POST", "/students", student_add), ("PATCH", "/students/{sid}", student_patch), ("DELETE", "/students/{sid}", student_delete),
        ("GET", "/students/{sid}/partner-candidates", partner_candidates), ("PUT", "/students/{sid}/partner", partner_put),
        ("PUT", "/students/{sid}/groups", student_groups), ("POST", "/students/{sid}/tier", student_tier),
        ("GET", "/clients", clients), ("PUT", "/students/{sid}/client", student_client),
        ("DELETE", "/students/{sid}/athlete", athlete_unlink),
        ("POST", "/teachers", teacher_add), ("PATCH", "/teachers/{tid}", teacher_patch), ("DELETE", "/teachers/{tid}", teacher_delete),
        ("PUT", "/teachers/{tid}/groups", teacher_groups), ("POST", "/teachers/{tid}/periods/{ym}/open", teacher_open_period),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, prefix + path, guard(handler))
    logger.info("Admin API (справочники): %d маршрутов", len(routes))
