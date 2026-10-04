"""HTTP API кабинета администратора Telegram Mini App — /api/admin/*.

Тонкий слой над сервисами бота: суммы, остатки и отметки оплат считает тот же
PaymentService, что и Telegram-экраны, поэтому Mini App и бот всегда сходятся.
Авторизация — `Authorization: tma <initData>` (подпись Telegram проверяется на
каждый запрос) и роль admin из листа users. Для локальной разработки без
Telegram — `MINIAPP_DEV_TG_ID` + заголовок `Authorization: dev` (на проде пусто).
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, cast

from aiohttp import web

from bot.handlers.admin.bills.helpers import _send_bill_to_parents, _student_group_names
from bot.models.enums import LessonType
from bot.services import payment_ledger
from bot.services.payment_methods import ADMIN_MANUAL
from bot.services.payment_service import debtors_summary, payment_lock, period_collection
from bot.services.pending_queue import rest_for_keys, settle_actions
from bot.services.profit_service import calculate_profit_lesson
from bot.services.student_service import has_short_tariff
from bot.utils.attendees import parse_attendees
from bot.services.rosters import group_members
from bot.utils.dates import current_period, last_periods
from bot.utils.locks import InProgressGuard
from bot.utils.telegram_auth import verify_init_data
from config.settings import settings
from bot.services.parent_notifier import notify_payment_confirmed
from bot.services.subscription_frequency import frequency_info, set_frequency

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


def _dp_get(dp, key: str):
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


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
    override_repo = dp["subscription_override_repo"]
    payment_repo = dp["payment_repo"]
    submission_repo = dp["submission_repo"]
    payment_service = dp["payment_service"]
    student_service = dp["student_service"]
    salary_service = dp["salary_service"]
    client_repo = dp["client_repo"]
    profit_service = dp["profit_service"]

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
        """Сводка: «требует внимания» (должники) → «сегодня» (занятия дня) → «месяц» (сбор оплат, прибыль).

        `?ym=` — плитки месяца (сбор оплат и прибыль) за прошлый месяц; должники и «сегодня» — всегда от текущего."""
        current = current_period()
        ym = request.query.get("ym") or ""
        period = ym if re.fullmatch(r"\d{4}-\d{2}", ym) and ym <= current else current
        prev = _prev_period(current)
        ledger = await payment_service.compute_ledger_map(since_period=settings.debtors_since_period or None)
        collected = period_collection(ledger, period)
        debtors_count, debtors_total = debtors_summary(ledger, current)
        today = date.today().isoformat()
        lessons_today = [ls for ls in await lesson_repo.get_all() if ls.date == today]
        day = await profit_service.get_lesson_summary(today)      # только занятия дня
        month = await profit_service.get_month_summary(period)    # как экран «Прибыль»
        return _json({
            "today": today, "period": period, "currentPeriod": current, "prevPeriod": prev,
            "debtorsCount": debtors_count, "debtorsTotal": debtors_total,
            "lessonsToday": len(lessons_today), "todayTeachers": await _today_by_teacher(lessons_today),
            "incomeToday": day.total_income, "profitToday": day.profit,
            "pendingTotal": collected.rest,
            "collected": {"accrued": collected.accrued, "paid": collected.paid,
                          "rest": collected.rest, "percent": collected.percent,
                          # сколько учеников ещё не оплатили текущий месяц (плитка «К оплате»)
                          "unpaidStudents": len({k[0] for k, (a, pd) in ledger.items() if k[2] == period and a > pd})},
            "incomeMonth": month.total_income, "salaryMonth": month.salary, "profitMonth": month.profit,
            "studentsCount": len(await student_repo.get_all()),
            "activityToday": await _activity_count(today),
            # колокольчик «События»: новые с последнего просмотра (?seen= — ts последнего просмотренного события)
            "activityNew": await _activity_count(request.query.get("seen") or today, strict=bool(request.query.get("seen"))),
        })

    async def pay_breakdown(request: web.Request, user) -> web.Response:
        """Оплаты месяца: филиалы → группы → ученики (плитка «Не оплатили за …»)."""
        from bot.services.payment_breakdown import month_breakdown
        return _json(await month_breakdown(dp, request.query.get("ym") or current_period()))

    async def _activity_count(since: str, strict: bool = False) -> int:
        repo = _dp_get(dp, "activity_repo")
        if repo is None:
            return 0
        try:
            events = await repo.since(since)
            return sum(1 for e in events if e.ts > since) if strict else len(events)
        except Exception as exc:                  # лента вспомогательная — сводка важнее
            logger.warning("Лента изменений недоступна: %s", exc)
            return 0

    # ── лента изменений ──────────────────────────────────────────────────
    _ID_RE = re.compile(r"\b(STU|GRP|TCH)-\d{4}\b")

    async def _activity_names() -> dict:
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        names.update({g.group_id: g.name for g in await group_repo.get_all(include_archived=True)})
        names.update({t.teacher_id: t.name for t in await teacher_repo.get_all()})
        return names

    async def _actor_labels(actors: set) -> dict:
        """tg_id → кто это: педагог по имени, администратор, родитель; 0 — система/ЮКасса."""
        users = {u.tg_id: u for u in await user_repo.get_all()}
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        out = {}
        for tg in actors:
            u = users.get(tg)
            if not tg:
                out[tg] = ""
            elif u is not None and u.teacher_id and not u.is_admin:
                out[tg] = teachers.get(u.teacher_id, "педагог")
            elif u is not None and u.is_admin:
                out[tg] = "админ" + (f" ({teachers[u.teacher_id]})" if u.teacher_id in teachers else "")
            else:
                out[tg] = "родитель"
        return out

    async def activity(request: web.Request, user) -> web.Response:
        """Все изменения за N дней: оплаты, занятия, заявки, выплаты, ученики, группы, педагоги."""
        repo = _dp_get(dp, "activity_repo")
        if repo is None:
            return _json({"error": "unavailable"}, status=503)
        try:
            days = max(1, min(int(request.query.get("days") or 1), 90))
        except ValueError:
            return _json({"error": "bad_request"}, status=400)
        kind = request.query.get("kind") or ""
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        events = [e for e in await repo.since(since) if not kind or e.kind == kind]
        names = await _activity_names()
        labels = await _actor_labels({e.actor for e in events})
        pretty = lambda text: _ID_RE.sub(lambda m: names.get(m.group(0), m.group(0)), text)  # noqa: E731
        out = [{"ts": e.ts, "kind": e.kind, "text": pretty(e.text),
                "who": labels.get(e.actor, "") or ("ЮКасса" if e.kind == "payment" and not e.actor else ""),
                "ref": e.ref, **_activity_link(e)} for e in events]
        return _json({"days": days, "since": since, "events": out, "latest": events[0].ts if events else ""})

    _YM_RE = re.compile(r"\b(20\d{2}-\d{2})\b")

    def _activity_link(e) -> dict:
        """Куда ведёт тап по событию: ученик (оплата — его счёт за месяц из текста), группа, педагог;
        `hasFile` — к событию приложен чек (ref `STU-… file:<file_id>`)."""
        ref = (e.ref or "").split(" file:")[0]
        out: dict = {"hasFile": " file:" in (e.ref or "")}
        ym = _YM_RE.search(e.text or "")
        if ref.startswith("STU-"):
            out.update({"studentId": ref, "ym": ym.group(1) if ym and e.kind == "payment" else ""})
        elif ref.startswith("GRP-"):
            out["groupId"] = ref
        elif ref.startswith("TCH-"):
            out["teacherId"] = ref
        return out

    async def activity_file(request: web.Request, user) -> web.Response:
        """Чек, приложенный к событию (ts + ref из ленты): скачивается через Bot API, file_id наружу не уходит."""
        repo = _dp_get(dp, "activity_repo")
        ts, ref = request.query.get("ts") or "", request.query.get("ref") or ""
        if repo is None or " file:" not in ref:
            return _json({"error": "not_found"}, status=404)
        if not any(e.ts == ts and e.ref == ref for e in await repo.get_all()):
            return _json({"error": "not_found"}, status=404)
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        try:
            file = await bot.get_file(ref.split(" file:", 1)[1])
            buf = await bot.download_file(file.file_path)
        except Exception as exc:
            logger.warning("Чек события %s не скачан: %s", ts, exc)
            return _json({"error": "file_unavailable"}, status=502)
        import mimetypes
        ctype = mimetypes.guess_type(file.file_path or "")[0] or "application/octet-stream"
        return web.Response(body=buf.read() if hasattr(buf, "read") else buf, content_type=ctype)

    async def _today_by_teacher(lessons_today: list) -> list[dict]:
        """Раскрывающийся список на сводке: педагог → его занятия за сегодня и прибыль школы.

        Деньги считаются как на экране «Прибыль» (`calculate_profit_lesson`): абонементные
        занятия без выручки в суммы не входят, но в списке видны — они тоже «отмечены сегодня».
        """
        teachers = {t.teacher_id: t for t in await teacher_repo.get_all()}
        groups = await _groups_by_id()
        students = {s.student_id: s.name for s in await student_repo.get_all()}
        blocks: dict[str, dict] = {}
        for ls in sorted(lessons_today, key=lambda x: x.recorded_at or ""):
            t = teachers.get(ls.teacher_id)
            prow = calculate_profit_lesson(ls, t) if t else None
            if ls.type == LessonType.GROUP:
                who = [students.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
                title = groups[ls.group_id].name if ls.group_id in groups else "Группа"
            else:
                who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
                title = " + ".join(who) or "Занятие"
            block = blocks.setdefault(ls.teacher_id, {
                "id": ls.teacher_id, "name": t.name if t else ls.teacher_name, "lessons": 0,
                "income": 0, "salary": 0, "profit": 0, "owner": bool(prow and prow.owner_income) or False,
                "ownerIncome": 0, "items": [],
            })
            block["lessons"] += 1
            if prow is not None:
                block["income"] += prow.income
                block["salary"] += prow.salary
                block["ownerIncome"] += prow.owner_income
                block["owner"] = block["owner"] or prow.owner_income > 0
            block["items"].append({
                "id": ls.lesson_id, "type": ls.type.value, "title": title, "durationMin": ls.duration_min,
                "attendees": len(who) if ls.type == LessonType.GROUP else 0,
                "billable": prow is not None,
                "income": prow.income if prow else 0,
                "salary": (prow.salary or prow.owner_income) if prow else 0,
            })
        for block in blocks.values():
            block["profit"] = block["income"] - block["salary"]
        return sorted(blocks.values(), key=lambda x: (-x["lessons"], x["name"]))

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

    async def student_lessons(request: web.Request, user) -> web.Response:
        """Занятия ученика за месяц (?ym=) — `student_lessons.month_lessons`."""
        sid = request.match_info["sid"]
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        from bot.api.student_lessons import month_lessons
        d = await month_lessons(dp, sid, request.query.get("ym") or current_period())
        return _json({"student": {"id": sid, "name": student.name}, **d})

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
                        "branch": g.branch_name, "mode": _mode(g.group) if g.group else None,
                        "hasShort": has_short_tariff(g.group),                            # где тариф вообще есть
                        **(await _frequency(sid, g.group))} for g in card.groups],
            "months": months, "debt": debt,
        })

    async def _frequency(sid: str, group) -> dict:
        return await frequency_info(override_repo, sid, group)

    async def student_frequency(request: web.Request, user) -> web.Response:
        """PUT {groupId, times: 2|3, since: YYYY-MM} — см. bot/services/subscription_frequency.py."""
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        ok = await set_frequency(override_repo, request.match_info["sid"], body.get("groupId"), body.get("times"),
                                 str(body.get("since") or ""), user.tg_id)
        return _json({"ok": True} if ok else {"error": "bad_request"}, status=200 if ok else 400)

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
        return _json({"teachers": out, "prevPeriod": prev,
                      "periodSubmit": settings.teacher_period_submit_enabled})   # 🟢/🔴 только при включённой сдаче

    async def teacher_card(request: web.Request, user) -> web.Response:
        tid = request.match_info["tid"]
        t = await teacher_repo.get_by_id(tid)
        if t is None:
            return _json({"error": "not_found"}, status=404)
        period = current_period()
        groups = await _groups_by_id()
        gids = [r.group_id for r in await dp["teacher_group_repo"].get_all() if r.teacher_id == tid]
        subs = (sorted({s.period_month for s in await submission_repo.get_by_teacher(tid)}, reverse=True)
                if settings.teacher_period_submit_enabled else [])       # сдача выключена — блока нет
        return _json({
            "id": t.teacher_id, "name": t.name, "tgId": t.tg_id,
            "rates": {"group": t.rate_group, "teacher": t.rate_for_teacher, "student": t.rate_for_student},
            "groups": [{"id": g, "name": groups[g].name} for g in gids if g in groups],
            "submitted": subs, "period": period,
            "salary": await salary_service.total_for(t, period),
            "isOwner": tid in settings.owner_teacher_id_set,
            "directPay": tid in settings.direct_pay_teacher_id_set,
            "periodSubmit": settings.teacher_period_submit_enabled,
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
        # одна карта «начислено/оплачено» на всех: счёт по каждому ученику отдельно («Все ученики» —
        # 150 расчётов по всем занятиям) открывал экран несколько секунд
        ledger = await payment_service.compute_ledger_map(since_period=period, until_period=period)
        totals: dict[str, dict] = {}
        for (sid, _tid, ym), (accrued, paid) in ledger.items():
            if ym != period:
                continue
            t = totals.setdefault(sid, {"total": 0, "paid": 0, "rest": 0})
            t["total"] += accrued
            t["paid"] += min(paid, accrued)
            t["rest"] += max(accrued - paid, 0)
        out = [{"id": s.student_id, "name": s.name, **totals.get(s.student_id, {"total": 0, "paid": 0, "rest": 0})}
               for s in members]
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
        async with payment_lock(sid, period):             # общий замок с педагогом и очередью решений
            # экран мог устареть (оплату уже отметил другой админ или педагог): лишнее не проводим молча
            rest = rest_for_keys(await payment_service.ledger_for(student, period), [key])
            if amount > rest and not bool(body.get("force")):
                return _json({"error": "overpay", "needsConfirm": True, "amount": amount, "rest": rest}, status=409)
            credited, rows = await payment_service.record_payment(
                sid, student.name, period, amount, user.tg_id, [key], "отмечено вручную", method,
                lesson_ids=lessons,
            )
        logger.info("Mini App: админ %s отметил оплату %d руб.: %s %s %s", user.tg_id, credited, sid, period, key)
        # заявка родителя (наличные/чек), которую покрыла эта отметка, уходит из «Ждут решения»
        await settle_actions(_dp_get(dp, "pending_repo"), payment_service, student, period, credited, user.tg_id)
        await notify_payment_confirmed(_dp_get(dp, "notifier"), student, period, credited)
        return _json({"credited": credited, "rows": rows, "overpaid": max(0, credited - rest)})

    async def pay_cancel(request: web.Request, user) -> web.Response:
        """«Убрать оплату»: снять ошибочную отметку — строка удаляется, остаток месяца пересчитывается."""
        pid = request.match_info["pid"]
        row = await payment_service.cancel_payment(pid, user.tg_id, (request.query.get("reason") or "")[:120])
        if row is None:
            return _json({"error": "not_found", "message": "Оплата не найдена или уже снята"}, status=404)
        logger.info("Mini App: админ %s снял оплату %s — %d ₽, %s %s", user.tg_id, pid, row.total_amount,
                    row.student_id, row.period_month)
        return _json({"ok": True, "amount": row.total_amount, "studentId": row.student_id, "period": row.period_month})

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
            payment = await payment_repo.get_by_id(payment_id)
            ok = await payment_service.confirm_payment(payment_id, user.tg_id, method)
        finally:
            _confirming.discard(payment_id)
        if ok and payment is not None:
            student = await student_repo.get_by_id(payment.student_id)
            await settle_actions(_dp_get(dp, "pending_repo"), payment_service, student, payment.period_month,
                                 payment.total_amount, user.tg_id)
            await notify_payment_confirmed(_dp_get(dp, "notifier"), student, payment.period_month, payment.total_amount)
        return _json({"ok": ok})

    # ── счёт ученика ─────────────────────────────────────────────────────
    async def bill(request: web.Request, user) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        student = await student_repo.get_by_id(sid)
        if student is None:
            return _json({"error": "not_found"}, status=404)
        bills, summary = await _student_bill(sid, period)
        pay_rows = await payment_repo.get_by_student_and_period(sid, period)
        paid_map, linked = payment_ledger.paid_sums(pay_rows), payment_ledger.paid_lesson_ids(pay_rows)
        rows = []
        for key, agg in bills.items():
            paid = paid_map.get(key, 0)
            rows.append({
                "key": key, "name": agg.name, "group": getattr(agg, "group", False), "subscription": agg.subscription,
                "total": agg.total, "paid": min(paid, agg.total), "rest": max(agg.total - paid, 0),
                # галочки в счёте нажимаются: lessonId — чтобы отметить оплату именно за эти занятия
                "items": [{"lessonId": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                           "amount": m["amount"], "paid": m["paid"]}
                          for m in payment_ledger.lesson_marks(agg.items, paid, linked.get(key, set()))],
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
        groups_of = await student_group_repo.get_map_by_student()          # для фильтра по филиалу/группе
        out = []
        for sid, months in debt_map.items():
            s = students_by_id.get(sid)
            if s is None or not months:
                continue
            closed = sum(a for ym, a in months.items() if ym < period)
            out.append({"id": sid, "name": s.name, "months": dict(sorted(months.items())),
                        "closedTotal": closed, "currentTotal": months.get(period, 0), "hasParent": bool(s.parent_addrs),
                        "groups": list(groups_of.get(sid, []))})
        out.sort(key=lambda r: (-r["closedTotal"], -r["currentTotal"], r["name"]))
        return _json({"period": period, "debtors": out})

    routes = [
        ("GET", "/me", me), ("GET", "/home", home), ("GET", "/activity", activity),
        ("GET", "/pay/breakdown", pay_breakdown),
        ("GET", "/activity/file", activity_file),
        ("GET", "/students", students), ("GET", "/students/{sid}", student_card),
        ("GET", "/students/{sid}/lessons", student_lessons), ("PUT", "/students/{sid}/frequency", student_frequency),
        ("GET", "/teachers", teachers), ("GET", "/teachers/{tid}", teacher_card),
        ("GET", "/pay/groups", pay_groups), ("GET", "/pay/students", pay_students),
        ("GET", "/pay/student/{sid}", pay_student), ("GET", "/pay/marks/{sid}", pay_marks),
        ("POST", "/pay/confirm", pay_confirm), ("POST", "/pay/confirm-invoice", pay_confirm_invoice),
        ("DELETE", "/payments/{pid}", pay_cancel),
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
