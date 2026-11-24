"""Поиск и перерасчёт абонемента по справке в кабинете администратора."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from aiohttp import web

from bot.models.enums import GroupBillingMode
from bot.services import activity
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX, payment_lock
from bot.services.subscription_recalculation import (
    LESSONS_PER_SUBSCRIPTION, Recalculation, RecalculationConflict, override_version, recalculated_amount,
)
from bot.utils import now_str
from bot.utils.dates import period_label
from bot.utils.format import format_money


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _norm_name(name: str) -> str:
    return " ".join(name.casefold().replace("ё", "е").split())


def register_recalculation_routes(app: web.Application, dp, admin_only, prefix: str) -> None:
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    student_group_repo = dp["student_group_repo"]
    override_repo = dp["subscription_override_repo"]
    payment_service = dp["payment_service"]
    store = payment_service.subscription_recalculations

    async def search(request: web.Request, user) -> web.Response:
        if store is None:
            return _json({"error": "unavailable"}, status=503)
        q = _norm_name(request.query.get("q") or "")
        if not q:
            return _json({"students": []})
        matches = [(_norm_name(s.name), s) for s in await student_repo.get_all(include_archived=True)]
        matches = [(name, s) for name, s in matches if q in name]
        matches.sort(key=lambda pair: (not pair[0].startswith(q), pair[1].is_archived, pair[0], pair[1].student_id))
        found = matches[:20]
        student_ids = {s.student_id for _, s in found}
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)
                  if g.billing_mode == GroupBillingMode.SUBSCRIPTION}
        memberships: dict[str, set[str]] = defaultdict(set)
        for row in await student_group_repo.get_all():
            if row.student_id in student_ids and row.group_id in groups:
                memberships[row.student_id].add(row.group_id)
        return _json({"students": [{
            "id": s.student_id, "name": s.name, "archived": s.is_archived,
            "groups": [{"id": gid, "name": groups[gid]}
                       for gid in sorted(memberships[s.student_id], key=lambda gid: (groups[gid], gid))],
        } for _, s in found]})

    async def subscription_recalculate(request: web.Request, user) -> web.Response:
        """Просмотр расчёта, затем подтверждение для того же состояния абонемента и оплаты."""
        if store is None:
            return _json({"error": "unavailable"}, status=503)
        sid = request.match_info["sid"]
        try:
            body = await request.json()
            gid, period = body["groupId"], body["ym"]
            start, end, missed = body["start"], body["end"], body["missed"]
            if not isinstance(gid, str) or type(missed) is not int or not 1 <= missed <= LESSONS_PER_SUBSCRIPTION:
                raise ValueError
            if (date.fromisoformat(period + "-01").isoformat()[:7] != period
                    or date.fromisoformat(start).isoformat() != start
                    or date.fromisoformat(end).isoformat() != end or start > end):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return _json({"error": "bad_request", "message": "Проверь месяц, даты справки и число пропусков от 1 до 8"},
                         status=400)
        student = await student_repo.get_by_id(sid)
        group = await group_repo.get_by_id(gid)
        if (student is None or group is None or group.billing_mode != GroupBillingMode.SUBSCRIPTION
                or gid not in await student_group_repo.get_groups_for_student(sid, include_left=True)):
            return _json({"error": "not_found"}, status=404)
        key = f"{SUBSCRIPTION_KEY_PREFIX}{gid}"
        async with payment_lock(sid, period):
            history = [r for r in await store.rows() if r.group_id == gid and r.student_id == sid]
            matching = next((r for r in history if start <= r.end and end >= r.start), None)
            if matching is not None:
                if (period, start, end, missed) != (matching.period, matching.start, matching.end, matching.missed):
                    return _json({"error": "overlap", "message": f"Эти даты уже учтены за {period_label(matching.period).lower()}"},
                                 status=409)
                if "quote" in body:
                    await payment_service.ledger_for(student, period, sync_key=key)
                return _json({"applied": True, "duplicate": True, "amount": matching.amount})
            ledger = (await payment_service.ledger_for(student, period, sync=False)).get(key)
            if ledger is None:
                return _json({"error": "no_subscription", "message": "За этот месяц у ученика нет абонемента этой группы"},
                             status=409)
            month = [r for r in history if r.period == period]
            manual = next((r for r in await override_repo.get_for_group(gid)
                           if r.period_month == period and r.student_id == sid), None)
            version = override_version(manual)
            active = [r for r in month if r.override_version == version]
            base = active[-1].base if active else ledger.accrued
            missed_total = missed + sum(r.missed for r in active)
            if missed_total > LESSONS_PER_SUBSCRIPTION:
                return _json({"error": "too_many", "message": "По справкам за один абонемент можно учесть до 8 пропусков"},
                             status=409)
            amount = recalculated_amount(base, missed_total)
            if ledger.paid > amount:
                return _json({"error": "paid_more", "message": f"Уже оплачено {format_money(ledger.paid)}, "
                              f"а после перерасчёта было бы {format_money(amount)}. Выбери другой месяц"}, status=409)
            quote = (
                f"{sid}:{gid}:{period}:{start}:{end}:{missed}:{len(month)}:"
                f"{version}:{base}:{ledger.accrued}:{ledger.paid}:{amount}"
            )
            result = {"base": base, "before": ledger.accrued, "amount": amount, "paid": ledger.paid,
                      "missedTotal": missed_total, "rest": amount - ledger.paid, "quote": quote}
            if "quote" not in body:
                return _json(result)
            if body["quote"] != quote:
                return _json({"error": "conflict", "message": "Сумма абонемента или оплата изменилась. Рассчитай заново"},
                             status=409)
            try:
                await store.add(Recalculation(gid, period, sid, start, end, missed, base, amount, version, user.tg_id,
                                              now_str()), len(month))
            except RecalculationConflict:
                return _json({"error": "conflict", "message": "Справка или абонемент обновились. Рассчитай заново"}, status=409)
            await payment_service.ledger_for(student, period, sync_key=key)
        await activity.record(activity.PAYMENT,
                              f"Перерасчёт абонемента {sid} в {gid} за {period}: {ledger.accrued} → {amount} ₽"
                              f" · справка {start}–{end} · пропущено {missed}",
                              actor=user.tg_id, ref=sid)
        return _json({**result, "applied": True})

    app.router.add_get(prefix + "/recalculation/search", admin_only(search))
    app.router.add_post(prefix + "/recalculation/{sid}", admin_only(subscription_recalculate))
