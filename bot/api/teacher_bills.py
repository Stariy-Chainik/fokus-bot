"""Счета своих групп, приём чеков/наличных, заявки на новых детей и приглашения — `/api/teacher/bills*`.

Выделено из `bot/api/teacher.py`, чтобы разгрузить основной модуль кабинета педагога.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
import time
from types import SimpleNamespace
from typing import Any, Callable, cast
import uuid

from aiogram.types import BufferedInputFile
from aiohttp import web

from bot.handlers.admin.bills.helpers import _send_bill_to_parents, _student_group_names
from bot.models.enums import GroupBillingMode, PaymentStatus
from bot.repositories.pending_action_repo import KIND_CASH, KIND_RECEIPT
from bot.services import activity, payment_ledger
from bot.services.group_invite import group_invite
from bot.services.new_child import (
    KIND_NEWCHILD, approve_new_child, existing_for, group_of, may_decide, reject_new_child,
)
from bot.services.parent_notifier import notify_payment_confirmed
from bot.services.payment_methods import ADMIN_MANUAL, CASH, RECEIPT_BANK
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX, payment_lock
from bot.services.pending_queue import rest_for_keys, settle_actions
from bot.services.rosters import group_members
from bot.services.subscription_recalculation import (
    LESSONS_PER_SUBSCRIPTION, Recalculation, RecalculationConflict, override_version, recalculated_amount,
)
from bot.utils import now_str
from bot.utils.dates import current_period, last_periods, period_label, periods_since
from bot.utils.format import format_money
from bot.utils.notify import notify
from config.settings import settings

logger = logging.getLogger(__name__)

# Чеки переводов, прикреплённые педагогами: receiptId → (teacher_id, student_id, ts, file_id). В памяти:
# чек нужен только на время отметки оплаты (6 часов), сам файл остаётся в Telegram у администраторов.
_teacher_receipts: dict[str, tuple[str, str, float, str]] = {}


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _dp_get(dp, key: str):
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


@dataclass(frozen=True)
class TeacherBillingHelpers:
    """Хелперы счёта/очереди, которые используют сводка и карточка ученика в `teacher.py`."""
    bills_summary: Callable
    student_debt: Callable
    queue_scope: Callable
    queue_count: Callable
    new_child_open: Callable
    held_cash: Callable
    sub_edit_group_ids: Callable
    can_recalculate: Callable
    full_bill: Callable


def billing_routes(
    dp: Any,
    own_group_ids: Callable,
    group_names: Callable,
    bot: Any = None,
) -> tuple[list[tuple[str, str, Any]], TeacherBillingHelpers]:
    """Собирает маршруты счетов/заявок педагога и возвращает `(routes, helpers)`."""
    user_repo = dp["user_repo"]
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    branch_repo = dp["branch_repo"]
    lesson_repo = dp["lesson_repo"]
    student_group_repo = dp["student_group_repo"]
    teacher_group_repo = dp["teacher_group_repo"]
    payment_repo = dp["payment_repo"]
    payment_service = dp["payment_service"]
    client_repo = dp["client_repo"]
    visibility = dp["visibility"]
    notifier = _dp_get(dp, "notifier")

    def billing_only(handler):
        async def wrapped(request: web.Request, user, teacher) -> web.Response:
            if teacher.teacher_id not in settings.billing_teacher_id_set:
                return _json({"error": "forbidden"}, status=403)
            return await handler(request, user, teacher)
        return wrapped

    def _can_recalculate(teacher) -> bool:
        return payment_service.subscription_recalculations is not None and (
            teacher.teacher_id in settings.subscription_recalc_teacher_id_set
        )

    async def _sub_edit_group_ids(teacher) -> set:
        """Где педагог ставит ученику абонемент на месяц: педагог со счетами — во всех своих группах,
        остальные — только в своих группах из SUBSCRIPTION_EDIT_GROUPS (решение владельца 09.10.2026)."""
        own = await own_group_ids(teacher)
        if teacher.teacher_id in settings.billing_teacher_id_set:
            return own
        return own & settings.subscription_edit_group_map.get(teacher.teacher_id, set())

    async def _queue_scope(user) -> set:
        """Ученики, чьи заявки об оплате педагог решает сам: все ученики его групп."""
        members: set[str] = set()
        for gid in await teacher_group_repo.get_groups_for_teacher(user.teacher_id):
            members.update(await student_group_repo.get_students_for_group(gid))
        return members

    async def _queue_count(teacher, user) -> int | None:
        pending_repo = _dp_get(dp, "pending_repo")
        if teacher.teacher_id not in settings.full_bill_teacher_id_set or pending_repo is None:
            return None
        members = await _queue_scope(user)
        # наличные, которые уже «у меня», решения педагога не ждут — их зачтёт администратор
        return sum(1 for a in await pending_repo.get_open()
                   if a.kind in (KIND_CASH, KIND_RECEIPT) and a.student_id in members and not a.held_by)

    async def _new_child_open(teacher) -> list:
        """Открытые заявки «ребёнка нет в списке» по своим группам (решение владельца 22.10.2026: решать и в кабинете)."""
        pending_repo = _dp_get(dp, "pending_repo")
        if pending_repo is None:
            return []
        own = await own_group_ids(teacher)
        return sorted((a for a in await pending_repo.get_open() if a.kind == KIND_NEWCHILD and group_of(a) in own),
                      key=lambda a: a.created_at)

    async def _held_cash(teacher) -> int:
        """Наличные у педагога, которые ещё не зачёл администратор (не переданы)."""
        pending_repo = _dp_get(dp, "pending_repo")
        if pending_repo is None:
            return 0
        return sum(a.amount for a in await pending_repo.get_open()
                   if a.kind == KIND_CASH and a.held_by == teacher.teacher_id)

    async def _full_bill(teacher, sid: str) -> bool:
        """Полный счёт ученика: педагог из FULL_BILL_TEACHER_IDS или ученик в группе из FULL_BILL_GROUPS."""
        if teacher.teacher_id in settings.full_bill_teacher_id_set:
            return True
        gids = settings.full_bill_group_map.get(teacher.teacher_id)
        return bool(gids and gids & set(await student_group_repo.get_groups_for_student(sid)))

    async def _visible_marks(teacher, sid: str, period: str) -> dict:
        """Счёт ученика глазами педагога — только его направления (решение владельца 29.09.2026).

        key → (agg, marks): абонемент — только своих групп; занятия — те, что прошли в его группах,
        или его индивидуальные. Бальные танцы той же девочки у другого педагога не видны.
        """
        # свои направления — все группы педагога, включая служебные (индивидуальные revenue-share Яковлевой)
        own = set(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id))
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        pay_rows = await payment_repo.get_by_student_and_period(sid, period)
        paid_map, linked = payment_ledger.paid_sums(pay_rows), payment_ledger.paid_lesson_ids(pay_rows)
        lesson_group = {ls.lesson_id: ls.group_id or "" for ls in await lesson_repo.get_by_student_and_period(sid, period)}
        full = await _full_bill(teacher, sid)             # полный счёт (FULL_BILL_TEACHER_IDS / FULL_BILL_GROUPS)
        out: dict[str, tuple] = {}
        for key, agg in bills.items():
            if agg.subscription:
                if full or key.split(":", 1)[-1] in own:
                    out[key] = (agg, None)
                continue
            marks = [m for m in payment_ledger.lesson_marks(agg.items, paid_map.get(key, 0), linked.get(key, set()))
                     if full or lesson_group.get(m["lesson_id"], "") in own
                     or (not lesson_group.get(m["lesson_id"]) and key == teacher.teacher_id)]
            if marks:
                out[key] = (agg, marks)
        return out

    async def _bill_rows(sid: str, period: str, teacher=None, viewer_tg_id: int | None = None) -> tuple[dict, dict, list]:
        visible = await _visible_marks(teacher, sid, period)
        own_groups = await own_group_ids(teacher) if teacher else set()
        pays = await payment_repo.get_by_student_and_period(sid, period)
        paid_map = payment_ledger.paid_sums(pays)
        rows = []
        for key, (agg, marks) in visible.items():
            if marks is None:                             # абонемент своей группы — целиком
                paid = min(paid_map.get(key, 0), agg.total)
                total, items = agg.total, [{"date": m["date"], "durationMin": m["duration_min"], "amount": m["amount"],
                                            "paid": m["paid"]} for m in payment_ledger.lesson_marks(agg.items, paid)]
            else:
                total = sum(m["amount"] for m in marks)
                # видны все занятия начисления — оплачено по деньгам (как у родителя и администратора: частичная
                # оплата 500 ₽ за урок в 2000 ₽ даёт остаток 1500, а не 2000); видна часть — по целым занятиям
                paid = (min(paid_map.get(key, 0), total) if len(marks) == len(agg.items)
                        else sum(m["amount"] for m in marks if m["paid"]))
                items = [{"lessonId": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                          "amount": m["amount"], "paid": m["paid"]} for m in marks]
            rows.append({"key": key, "name": agg.name, "subscription": agg.subscription, "total": total,
                         "paid": paid, "rest": max(total - paid, 0), "items": items,
                         # абонемент своей группы — педагог может поменять сумму за месяц («Абонемент за этот месяц»)
                         "ownGroup": bool(agg.subscription and key.split(":", 1)[-1] in own_groups),
                         # оплаты по начислению; mine — отметил сам педагог, такую он может снять («Убрать оплату»)
                         "paidRows": [{"id": x.payment_id, "amount": x.total_amount, "date": (x.paid_at or "")[:10],
                                       "method": x.payment_method or "",
                                       "mine": bool(viewer_tg_id and x.confirmed_by_tg_id == viewer_tg_id)}
                                      for x in pays if x.teacher_id == key and x.status == PaymentStatus.PAID and x.total_amount > 0]})
        summary = {"total": sum(r["total"] for r in rows), "paid": sum(r["paid"] for r in rows),
                   "rest": sum(r["rest"] for r in rows)}
        return visible, summary, rows

    async def _bills_summary(teacher, period: str) -> dict:
        """Счета своих групп за месяц: сколько учеников с остатком и сумма — строка «требует внимания».

        Только свои направления: абонементы своих групп и собственные начисления педагога.
        """
        members: set[str] = set()
        own = await own_group_ids(teacher)
        for gid in own:
            members.update(await student_group_repo.get_students_for_group(gid))
        full = {sid for sid in members if await _full_bill(teacher, sid)}
        keys = {teacher.teacher_id} | {f"SUB:{g}" for g in own}
        ledger = await payment_service.compute_ledger_map(since_period=period)
        rest_by_student: dict[str, int] = {}
        for (sid, tid, ym), (accrued, paid) in ledger.items():
            if ym == period and sid in members and (sid in full or tid in keys) and accrued > paid:
                rest_by_student[sid] = rest_by_student.get(sid, 0) + accrued - paid
        return {"students": len(rest_by_student), "rest": sum(rest_by_student.values())}

    async def _student_debt(teacher, sid: str) -> dict:
        """«Сколько должен ученик всего» в карточке у педагога со счетами (решение владельца 06.11.2026)."""
        months = []
        for ym in periods_since(settings.parent_bills_since_period):
            _visible, summary, _rows = await _bill_rows(sid, ym, teacher)
            if summary["total"] or summary["paid"]:
                months.append({"ym": ym, **summary})
        months.reverse()
        return {"total": sum(m["rest"] for m in months), "months": months}

    async def group_invite_view(request: web.Request, user, teacher) -> web.Response:
        """«🔗 Пригласить родителей» своей группы: короткий текст со ссылками (Telegram, MAX)."""
        gid = request.match_info["gid"]
        if gid not in await own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        return _json(await group_invite(gid, (await group_names()).get(gid, gid), bot))

    async def newchild_list(request: web.Request, user, teacher) -> web.Response:
        names = await group_names()
        groups_of: dict[str, list[str]] = {}
        for m in await student_group_repo.get_all():
            if m.is_active:
                groups_of.setdefault(m.student_id, []).append(str(names.get(m.group_id, m.group_id)))
        out = []
        for a in await _new_child_open(teacher):
            ex = await existing_for(student_repo, student_group_repo, a)
            out.append({
                "id": a.action_id, "name": a.student_name, "groupName": names.get(group_of(a), group_of(a)),
                "parent": "MAX" if a.parent_addr.startswith("m") else "Telegram",
                "sender": a.comment.split(" · ", 1)[1] if " · " in a.comment else "", "createdAt": a.created_at,
                "existing": {"id": ex.student_id, "name": ex.name, "groups": groups_of.get(ex.student_id, []),
                             "chosen": bool(a.student_id)} if ex else None,
            })
        return _json({"items": out})

    async def newchild_decide(request: web.Request, user, teacher) -> web.Response:
        """Та же заявка, что кнопками в Telegram: одобрить (карточка или привязка к существующему) / отклонить."""
        pending_repo = _dp_get(dp, "pending_repo")
        action = await pending_repo.get_by_id(request.match_info["aid"]) if pending_repo else None
        if action is None or action.kind != KIND_NEWCHILD or not await may_decide(user, action, teacher_group_repo):
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = None
        if bool((body or {}).get("approve")):
            res = await approve_new_child(pending_repo, student_repo, student_group_repo, notifier, action, user.tg_id)
            if res is None:
                return _json({"error": "already_decided"}, status=409)
            if bot is not None:
                names = await group_names()
                await notify(bot, [u.tg_id for u in await user_repo.get_admins()],
                             f"➕ Педагог {teacher.name} одобрил заявку родителя\nУченик: {res.student.name}"
                             f"{' (уже был в базе)' if res.existing else ' (новая карточка)'}\n"
                             f"Группа: {names.get(group_of(action), group_of(action))}")
            return _json({"ok": True, "studentId": res.student.student_id, "name": res.student.name, "existing": res.existing})
        if not await reject_new_child(pending_repo, notifier, action, user.tg_id):
            return _json({"error": "already_decided"}, status=409)
        return _json({"ok": True, "rejected": True})

    @billing_only
    async def unpaid(request: web.Request, user, teacher) -> web.Response:
        """Оплаты месяца по своим группам — как плитка «не оплатили за …» у администратора."""
        from bot.services.payment_breakdown import month_breakdown
        period = request.query.get("ym") or current_period()
        own = await own_group_ids(teacher)
        names = await group_names()
        branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
        by_branch = {g.group_id: g.branch_id for g in await group_repo.get_all(include_archived=True)}
        breakdown = {g["id"]: g for b in (await month_breakdown(dp, period))["branches"] for g in b["groups"]}
        out = []
        for gid in sorted(own, key=lambda g: names.get(g, g)):
            members = set(await student_group_repo.get_students_for_group(gid))
            src = breakdown.get(gid, {"students": []})
            students = []
            for st in src["students"]:
                if st["id"] not in members:
                    continue
                if not await _full_bill(teacher, st["id"]):       # свои направления, как в счёте педагога
                    _v, summary, rows = await _bill_rows(st["id"], period, teacher)
                    if not summary["total"]:
                        continue
                    sub_ = sum(r["total"] for r in rows if r["subscription"])
                    st = {**st, "accrued": summary["total"], "paid": summary["paid"], "rest": summary["rest"],
                          "totalRest": summary["rest"],          # чужие направления педагогу не показываем
                          "sub": sub_, "group": 0, "ind": summary["total"] - sub_,
                          "status": "paid" if not summary["rest"] else "partial" if summary["paid"] else "unpaid"}
                students.append(st)
            if not students:
                continue
            students.sort(key=lambda x: (-x["rest"], x["name"]))
            out.append({"id": gid, "name": names.get(gid, gid), "branchName": branches.get(by_branch.get(gid, ""), ""),
                        "students": students, "accrued": sum(x["accrued"] for x in students),
                        "paid": sum(x["paid"] for x in students), "rest": sum(x["rest"] for x in students),
                        "unpaid": sum(1 for x in students if x["rest"])})
        out.sort(key=lambda g: (-g["rest"], g["name"]))
        return _json({"period": period, "groups": out,
                      "accrued": sum(g["accrued"] for g in out), "paid": sum(g["paid"] for g in out),
                      "rest": sum(g["rest"] for g in out),
                      "unpaidStudents": len({s["id"] for g in out for s in g["students"] if s["rest"]})})

    @billing_only
    async def bills_groups(request: web.Request, user, teacher) -> web.Response:
        period = request.query.get("ym") or current_period()
        gids = await own_group_ids(teacher)
        groups_ = [g for g in await group_repo.get_all() if g.group_id in gids]
        out = [{"id": g.group_id, "name": g.name, "mode": g.billing_mode.value,
                "students": len(await student_group_repo.get_students_for_group(g.group_id))}
               for g in sorted(groups_, key=lambda g: g.name)]
        return _json({"period": period, "groups": out, "periods": last_periods(3)})

    @billing_only
    async def bills_group(request: web.Request, user, teacher) -> web.Response:
        gid = request.match_info["gid"]
        period = request.query.get("ym") or current_period()
        if gid not in await own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        g = await group_repo.get_by_id(gid)
        rows = []
        for s in await group_members(student_repo, student_group_repo, gid):
            _b, summary, _r = await _bill_rows(s.student_id, period, teacher)
            rows.append({"id": s.student_id, "name": s.name, "hasParent": bool(s.parent_addrs), **summary})
        return _json({"group": {"id": gid, "name": g.name if g else gid}, "period": period, "students": rows})

    @billing_only
    async def bills_student(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        _bills, summary, rows = await _bill_rows(sid, period, teacher, viewer_tg_id=user.tg_id)
        return _json({"student": {"id": sid, "name": s.name, "hasParent": bool(s.parent_addrs)},
                      "period": period, "rows": rows, **summary,
                      "canRecalculate": _can_recalculate(teacher),
                      "groups": await _student_group_names(sid, student_group_repo, group_repo)})

    @billing_only
    async def bills_marks(request: web.Request, user, teacher) -> web.Response:
        """Занятия ученика по одному начислению с ✅/⬜ — как экран отметки оплат у админа."""
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        key = request.query.get("key", "")
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        marks, ledger = await payment_service.teacher_lesson_marks(s, period, key)
        visible = (await _visible_marks(teacher, sid, period)).get(key)
        if ledger is None or visible is None:
            return _json({"error": "not_found"}, status=404)
        if visible[1] is not None:                         # только занятия своих направлений
            ids = {m["lesson_id"] for m in visible[1]}
            marks = [m for m in marks if m["lesson_id"] in ids]
        accrued = sum(m["amount"] for m in marks)
        paid_v = sum(m["amount"] for m in marks if m["paid"])
        return _json({
            "student": {"id": sid, "name": s.name}, "period": period,
            "ledger": {"key": key, "name": ledger.name, "group": ledger.group, "accrued": accrued,
                       "paid": paid_v, "remainder": accrued - paid_v},
            "marks": [{"lessonId": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                       "amount": m["amount"], "paid": m["paid"]} for m in marks],
        })

    async def bills_sub_amount(request: web.Request, user, teacher) -> web.Response:
        """«Абонемент за этот месяц»: педагог ставит ученику своей группы сумму абонемента на месяц."""
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        period = (body or {}).get("ym") or current_period()
        gid, amount = (body or {}).get("groupId"), (body or {}).get("amount")
        reason = str((body or {}).get("reason") or "")[:120]
        if not isinstance(gid, str) or not isinstance(amount, int) or amount < 0:
            return _json({"error": "bad_request", "message": "Нужны группа и сумма"}, status=400)
        if gid not in await _sub_edit_group_ids(teacher) or not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        group = await group_repo.get_by_id(gid)
        if s is None or group is None or group.billing_mode != GroupBillingMode.SUBSCRIPTION \
                or gid not in await student_group_repo.get_groups_for_student(sid, include_left=True):
            return _json({"error": "not_found"}, status=404)
        key = f"{SUBSCRIPTION_KEY_PREFIX}{gid}"
        from bot.utils.format import format_amount
        async with payment_lock(sid, period):
            led = (await payment_service.ledger_for(s, period)).get(key)
            old = led.accrued if led else group.price_full
            if led and led.paid > amount:
                return _json({"error": "paid_more", "message": f"За этот месяц уже оплачено {format_money(led.paid)} — "
                              f"сумму ниже оплаченного поставить нельзя"}, status=409)
            if old == amount:
                return _json({"ok": True, "old": old, "amount": amount, "unchanged": True})
            await dp["subscription_override_repo"].upsert(gid, period, sid, amount)
            await payment_service.ledger_for(s, period)
        await activity.record(activity.PAYMENT, f"Абонемент {sid} в {gid} за {period}: {format_amount(old)} → {format_money(amount)}"
                              f" · педагог {teacher.name}" + (f" · {reason}" if reason else ""), actor=user.tg_id, ref=sid)
        if bot is not None:
            text = (
                f"✏️ Педагог {teacher.name} изменил абонемент\nУченик: {s.name}\nГруппа: {group.name}\n"
                f"Месяц: {period_label(period)}\nБыло {format_money(old)} → стало {format_money(amount)}"
                + (f"\nПричина: {reason}" if reason else "")
            )
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()], text)
        logger.info("Mini App: педагог %s изменил абонемент %s %s %s: %d → %d", teacher.teacher_id, sid, gid, period, old, amount)
        return _json({"ok": True, "old": old, "amount": amount})

    async def subscription_recalculate(request: web.Request, user, teacher) -> web.Response:
        """Просмотр расчёта, затем подтверждение для того же состояния абонемента и оплаты."""
        if not _can_recalculate(teacher):
            return _json({"error": "forbidden"}, status=403)
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
        if gid not in await own_group_ids(teacher) or not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        student = await student_repo.get_by_id(sid)
        group = await group_repo.get_by_id(gid)
        if student is None or group is None or group.billing_mode != GroupBillingMode.SUBSCRIPTION:
            return _json({"error": "not_found"}, status=404)
        store = payment_service.subscription_recalculations
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
            manual = next((r for r in await dp["subscription_override_repo"].get_for_group(gid)
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
                              f" · справка {start}–{end} · пропущено {missed} · педагог {teacher.name}",
                              actor=user.tg_id, ref=sid)
        return _json({**result, "applied": True})

    @billing_only
    async def bills_cancel(request: web.Request, user, teacher) -> web.Response:
        """«Убрать оплату»: педагог снимает только свою отметку (confirmed_by — он сам) у видимого ученика."""
        pid = request.match_info["pid"]
        row = await payment_repo.get_by_id(pid)
        if row is None or not await visibility.is_visible(teacher.teacher_id, row.student_id):
            return _json({"error": "not_found"}, status=404)
        if row.confirmed_by_tg_id != user.tg_id:
            return _json({"error": "forbidden", "message": "Снять можно только свою отметку — напишите администратору"},
                         status=403)
        done = await payment_service.cancel_payment(pid, user.tg_id, (request.query.get("reason") or "")[:120],
                                                    only_by_tg_id=user.tg_id)
        if done is None:
            return _json({"error": "not_found", "message": "Оплата не найдена или уже снята"}, status=404)
        logger.info("Mini App: педагог %s снял свою оплату %s — %d ₽, %s %s", teacher.teacher_id, pid,
                    done.total_amount, done.student_id, done.period_month)
        return _json({"ok": True, "amount": done.total_amount})

    @billing_only
    async def bills_receipt(request: web.Request, user, teacher) -> web.Response:
        """Чек перевода к отметке оплаты педагогом (решение владельца 02.10.2026)."""
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        sid = request.match_info["sid"]
        try:
            form = await request.post()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        period = str(form.get("ym") or current_period())
        upload = form.get("file")
        if not hasattr(upload, "file"):
            return _json({"error": "bad_request", "message": "Прикрепите чек"}, status=400)
        data = upload.file.read()
        if not data or len(data) > 15 * 1024 * 1024:
            return _json({"error": "file_too_big" if data else "bad_request"}, status=400)
        ctype = (upload.content_type or "").lower()
        is_image = ctype.startswith("image/")
        if not is_image and ctype != "application/pdf":
            return _json({"error": "bad_file_type"}, status=400)
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        try:
            amount = int(str(form.get("amount") or 0))
        except ValueError:
            amount = 0
        from bot.utils.format import format_money
        caption = (f"🧾 Чек перевода от педагога {teacher.name}\nУченик: {s.name}\nПериод: {period_label(period)}"
                   + (f"\nСумма: {format_money(amount)}" if amount else ""))
        filename = upload.filename or ("receipt.jpg" if is_image else "receipt.pdf")
        file_id, sent = "", 0
        for admin in await user_repo.get_admins():
            try:
                media = file_id or BufferedInputFile(data, filename=filename)
                msg = (await bot.send_photo(admin.tg_id, media, caption=caption) if is_image
                       else await bot.send_document(admin.tg_id, media, caption=caption))
                if msg is not None and not file_id:
                    file_id = msg.photo[-1].file_id if is_image else msg.document.file_id
                sent += 1
            except Exception as exc:
                logger.warning("Чек педагога не ушёл админу %s: %s", admin.tg_id, exc)
        if not sent:
            return _json({"error": "bot_unavailable"}, status=503)
        now_ts = time.time()
        for rid in [k for k, rec in _teacher_receipts.items() if now_ts - rec[2] >= 6 * 3600]:
            _teacher_receipts.pop(rid, None)
        receipt_id = uuid.uuid4().hex
        _teacher_receipts[receipt_id] = (teacher.teacher_id, sid, now_ts, file_id)
        logger.info("Mini App: педагог %s прикрепил чек %s — %s %s, админов: %d",
                    teacher.teacher_id, receipt_id, sid, period, sent)
        return _json({"receiptId": receipt_id, "notified": sent})

    def _receipt_ok(receipt_id, teacher_id: str, sid: str) -> bool:
        rec = _teacher_receipts.get(receipt_id or "") if isinstance(receipt_id, str) else None
        return rec is not None and rec[0] == teacher_id and rec[1] == sid and time.time() - rec[2] < 6 * 3600

    @billing_only
    async def bills_pay(request: web.Request, user, teacher) -> web.Response:
        """Отметить оплату ученика своей группы: сумма зачитывается в остаток начисления."""
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        period = (body or {}).get("ym") or current_period()
        key, amount = (body or {}).get("key"), (body or {}).get("amount")
        method = (body or {}).get("method") or RECEIPT_BANK
        lessons = [x for x in ((body or {}).get("lessonIds") or []) if isinstance(x, str)]
        if not isinstance(key, str) or not key or not isinstance(amount, int) or amount <= 0:
            return _json({"error": "bad_request", "message": "Нужны начисление и сумма"}, status=400)
        if method in (CASH, ADMIN_MANUAL):
            return _json({"error": "cash_via_admin",
                          "message": "Наличные зачитывает администратор — отметьте «Приняла наличные»"}, status=400)
        if method != RECEIPT_BANK:
            return _json({"error": "bad_request", "message": "Неизвестный способ оплаты"}, status=400)
        if not _receipt_ok((body or {}).get("receiptId"), teacher.teacher_id, sid):
            return _json({"error": "receipt_required", "message": "Прикрепите чек перевода"}, status=400)
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        visible = (await _visible_marks(teacher, sid, period)).get(key)
        if visible is None:
            return _json({"error": "not_found"}, status=404)
        foreign = (visible[1] is not None and key != teacher.teacher_id
                   and not await _full_bill(teacher, sid))
        if foreign:
            ids = {m["lesson_id"] for m in visible[1] if not m["paid"]}
            if not lessons or not set(lessons) <= ids:
                return _json({"error": "bad_request", "message": "Отметьте занятия своего направления"}, status=400)
        async with payment_lock(sid, period):
            rest = rest_for_keys(await payment_service.ledger_for(s, period), [key])
            if foreign:
                rest = min(rest, sum(m["amount"] for m in visible[1] if not m["paid"]))
            if amount > rest:
                return _json({"error": "overpay", "needsConfirm": True, "amount": amount, "rest": rest,
                              "allowOverpay": False}, status=409)
            rec = _teacher_receipts.get((body or {}).get("receiptId") or "")
            credited, rows = await payment_service.record_payment(
                sid, s.name, period, amount, user.tg_id, [key],
                f"отметил педагог {teacher.name} · чек", method, lesson_ids=lessons,
                receipt_file_id=rec[3] if rec else "",
            )
        logger.info("Mini App: педагог %s отметил оплату %d ₽ — %s %s %s",
                    teacher.teacher_id, credited, sid, period, key)
        await settle_actions(_dp_get(dp, "pending_repo"), payment_service, s, period, credited, user.tg_id)
        await notify_payment_confirmed(notifier, s, period, credited)
        return _json({"credited": credited, "rows": rows, "overpaid": max(0, credited - rest)})

    @billing_only
    async def bills_cash(request: web.Request, user, teacher) -> web.Response:
        """«Приняла наличные»: не оплата, а заявка администратору «деньги у педагога»."""
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        period = (body or {}).get("ym") or current_period()
        parts = [p for p in ((body or {}).get("parts") or []) if isinstance(p, dict)]
        if not parts or any(not isinstance(p.get("key"), str) or not isinstance(p.get("amount"), int)
                            or p["amount"] <= 0 for p in parts):
            return _json({"error": "bad_request", "message": "Отметьте, за что приняли деньги"}, status=400)
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        visible = await _visible_marks(teacher, sid, period)
        full = await _full_bill(teacher, sid)
        for p in parts:
            v = visible.get(p["key"])
            if v is None:
                return _json({"error": "not_found"}, status=404)
            ids = [x for x in (p.get("lessonIds") or []) if isinstance(x, str)]
            if v[1] is not None and p["key"] != teacher.teacher_id and not full:
                own = {m["lesson_id"] for m in v[1] if not m["paid"]}
                if not ids or not set(ids) <= own:
                    return _json({"error": "bad_request", "message": "Отметьте занятия своего направления"}, status=400)
        total = sum(p["amount"] for p in parts)
        keys = [p["key"] for p in parts]
        async with payment_lock(sid, period):
            rest = rest_for_keys(await payment_service.ledger_for(s, period), keys)
            if total > rest:
                from bot.utils.format import format_money
                return _json({"error": "overpay", "message": f"К оплате осталось {format_money(rest)}", "rest": rest}, status=409)
            for p in parts:
                ids = [x for x in (p.get("lessonIds") or []) if isinstance(x, str)]
                if ids:
                    await payment_service.set_payment_intent(s, period, p["key"], ids)
            from bot.services.pending_queue import queue_action
            action = await queue_action(_dp_get(dp, "pending_repo"), KIND_CASH, s, period, amount=total,
                                        method=CASH, comment=f"принял педагог {teacher.name}",
                                        teacher_keys=keys, held_by=teacher.teacher_id)
        if action is None:
            return _json({"error": "unavailable"}, status=503)
        logger.info("Mini App: педагог %s принял наличные %d ₽ — %s %s (%s)",
                    teacher.teacher_id, total, sid, period, action.action_id)
        return _json({"ok": True, "actionId": action.action_id, "amount": total})

    @billing_only
    async def bills_student_send(request: web.Request, user, teacher) -> web.Response:
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        if not bills:
            return _json({"error": "nothing_to_send"}, status=409)
        names = await _student_group_names(sid, student_group_repo, group_repo)
        recipients, sent_to, _ = await _send_bill_to_parents(
            cast(Any, SimpleNamespace(bot=bot)), s, period, bills, names, payment_service, client_repo,
        )
        logger.info("Mini App: педагог %s отправил счёт %s за %s", teacher.teacher_id, sid, period)
        return _json({"recipients": recipients, "sentTo": sent_to})

    @billing_only
    async def bills_group_send(request: web.Request, user, teacher) -> web.Response:
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        gid = request.match_info["gid"]
        period = request.query.get("ym") or current_period()
        if gid not in await own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        sent = no_parent = failed = skipped = 0
        for s in await group_members(student_repo, student_group_repo, gid):
            bills = await payment_service.compute_bills_for_student_period(s.student_id, period)
            if not bills:
                skipped += 1
                continue
            names = await _student_group_names(s.student_id, student_group_repo, group_repo)
            recipients, sent_to, _ = await _send_bill_to_parents(
                cast(Any, SimpleNamespace(bot=bot)), s, period, bills, names, payment_service, client_repo,
            )
            if recipients == 0:
                no_parent += 1
            elif sent_to == 0:
                failed += 1
            else:
                sent += 1
        logger.info("Mini App: педагог %s разослал счета группы %s за %s — %d",
                    teacher.teacher_id, gid, period, sent)
        return _json({"sent": sent, "noParent": no_parent, "failed": failed, "skipped": skipped})

    routes = [
        ("GET", "/groups/{gid}/invite", group_invite_view),
        ("GET", "/newchild", newchild_list),
        ("POST", "/newchild/{aid}/decide", newchild_decide),
        ("GET", "/bills", bills_groups),
        ("GET", "/unpaid", unpaid),
        ("GET", "/bills/group/{gid}", bills_group),
        ("POST", "/bills/group/{gid}/send", bills_group_send),
        ("GET", "/bills/student/{sid}", bills_student),
        ("POST", "/bills/student/{sid}/send", bills_student_send),
        ("GET", "/bills/student/{sid}/marks", bills_marks),
        ("POST", "/bills/student/{sid}/pay", bills_pay),
        ("POST", "/bills/student/{sid}/cash", bills_cash),
        ("POST", "/bills/student/{sid}/receipt", bills_receipt),
        ("DELETE", "/payments/{pid}", bills_cancel),
        ("PUT", "/bills/student/{sid}/subscription", bills_sub_amount),
        ("POST", "/bills/student/{sid}/subscription/recalculation", subscription_recalculate),
    ]
    helpers = TeacherBillingHelpers(
        bills_summary=_bills_summary,
        student_debt=_student_debt,
        queue_scope=_queue_scope,
        queue_count=_queue_count,
        new_child_open=_new_child_open,
        held_cash=_held_cash,
        sub_edit_group_ids=_sub_edit_group_ids,
        can_recalculate=_can_recalculate,
        full_bill=_full_bill,
    )
    return routes, helpers
