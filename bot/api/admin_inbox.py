"""Очередь решений администратора — `/api/admin/inbox*`.

Показывает то, что ждёт решения: оплаты наличными и присланные чеки
(лист `pending_actions`) и заявки педагогов на новых учеников (лист
`student_requests`). Решение принимается здесь же и закрывает строку, поэтому
кнопки в Telegram-чате и кабинет не расходятся.

Чек показывается картинкой: файл лежит в Telegram, кабинет отдаёт его через
`/inbox/{id}/file` (Bot API `getFile` + скачивание), наружу file_id не уходит.
"""
from __future__ import annotations

import logging
import mimetypes

from aiohttp import web

from bot.repositories.pending_action_repo import (
    DONE, KIND_CASH, KIND_CHILD, KIND_RECEIPT, OPEN, REJECTED,
)
from bot.services.new_child import KIND_NEWCHILD, approve_new_child, reject_new_child
from bot.services.parent_notifier import notify_payment_confirmed, parse_addr
from bot.services.pending_queue import rest_for_keys
from bot.services.payment_service import payment_lock
from bot.services.parent_views import METHOD_LABELS
from bot.utils.dates import period_label

logger = logging.getLogger(__name__)

KIND_LABEL = {
    KIND_CASH: "Оплата наличными",
    KIND_RECEIPT: "Чек об оплате",
    KIND_CHILD: "Заявка на привязку ребёнка",
    KIND_NEWCHILD: "Ребёнка нет в группе — завести",
}


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def register_inbox_routes(app: web.Application, dp, admin_only, prefix: str, bot=None, scope=None) -> None:
    """scope — async (user) → множество student_id, чьи заявки об оплате видны (кабинет педагога,
    FULL_BILL_TEACHER_IDS): только наличные и чеки этих учеников, без привязок и заявок педагогов.
    None — администратор, видно всё."""
    pending_repo = dp["pending_repo"] if "pending_repo" in getattr(dp, "workflow_data", dp) else None
    student_repo = dp["student_repo"]
    payment_service = dp["payment_service"]
    request_repo = (getattr(dp, "workflow_data", dp)).get("student_request_repo")
    teacher_repo = (getattr(dp, "workflow_data", dp)).get("teacher_repo")

    async def _teacher_names() -> dict:
        return {t.teacher_id: t.name for t in await teacher_repo.get_all()} if teacher_repo else {}

    def _action_dto(a, rest: int | None = None, user=None, names: dict | None = None) -> dict:
        """Кнопки зависят от того, кто смотрит: наличные педагог не зачитывает (только «деньги у меня»),
        администратор зачитывает наличные у педагога, когда получит деньги."""
        teacher_view = scope is not None
        held = names.get(a.held_by, a.held_by) if a.held_by and names is not None else a.held_by
        title = KIND_LABEL.get(a.kind, a.kind)
        approve, reject, note = None, None, ""
        if a.kind == KIND_CASH and a.held_by:
            title = f"Наличные у педагога · {held}"
            if teacher_view:
                note = "Деньги у вас — передайте администратору, он зачтёт оплату"
            else:
                approve = f"✅ Деньги получены — зачесть{' ' + str(a.amount) + ' ₽' if a.amount else ''}"
                reject = "❌ Отклонить"
        elif a.kind == KIND_CASH and teacher_view:
            approve, reject = "✋ Деньги у меня", "❌ Денег не было"
        elif a.kind == KIND_NEWCHILD:
            approve, reject = "✅ Завести и привязать", "❌ Отклонить"
        return {
            "id": a.action_id, "kind": a.kind, "title": title,
            "heldBy": held or "", "approveLabel": approve, "rejectLabel": reject, "note": note,
            "studentId": a.student_id, "student": a.student_name,
            "period": a.period_month, "periodLabel": period_label(a.period_month) if a.period_month else "",
            "amount": a.amount, "method": METHOD_LABELS.get(a.method, a.method),
            "comment": a.comment, "createdAt": a.created_at,
            "hasFile": bool(a.file_id), "fileType": a.file_type, "rest": rest,
        }

    async def _allowed(user, action) -> bool:
        if scope is None:
            return True
        if action.held_by and action.held_by != user.teacher_id:      # чужие наличные у другого педагога
            return False
        return action.kind in (KIND_CASH, KIND_RECEIPT) and action.student_id in await scope(user)

    async def inbox(request: web.Request, user) -> web.Response:
        items = []
        names = await _teacher_names()
        if pending_repo is not None:
            for a in sorted(await pending_repo.get_open(), key=lambda x: x.created_at):
                if not await _allowed(user, a):
                    continue
                rest = None
                if a.period_month and a.kind in (KIND_CASH, KIND_RECEIPT):
                    student = await student_repo.get_by_id(a.student_id)
                    if student is not None:
                        ledgers = await payment_service.ledger_for(student, a.period_month, sync=False)
                        rest = sum(v.remainder for v in ledgers.values())
                items.append(_action_dto(a, rest, user, names))
        requests = []
        if request_repo is not None and scope is None:
            for r in await request_repo.get_pending():
                requests.append({
                    "id": r.request_id, "kind": "student_request",
                    "title": "Педагог просит завести ученика",
                    "student": r.student_name, "comment": f"от {r.teacher_name}",
                    "createdAt": r.created_at,
                })
        # наличные на руках у педагогов: сколько и у кого — контроль администратора
        held: dict[str, dict] = {}
        for it in items:
            if it["heldBy"]:
                h = held.setdefault(it["heldBy"], {"name": it["heldBy"], "amount": 0, "count": 0})
                h["amount"] += it["amount"] or 0
                h["count"] += 1
        actionable = sum(1 for it in items if it["approveLabel"] is not None or it["kind"] != KIND_CASH)
        return _json({"items": items, "requests": requests, "held": list(held.values()),
                      "total": (actionable if scope is not None else len(items)) + len(requests)})

    async def inbox_file(request: web.Request, user) -> web.Response:
        """Чек картинкой: отдаём файл из Telegram, не раскрывая file_id."""
        if pending_repo is None or bot is None:
            return _json({"error": "unavailable"}, status=503)
        action = await pending_repo.get_by_id(request.match_info["aid"])
        if action is None or not action.file_id or not await _allowed(user, action):
            return _json({"error": "not_found"}, status=404)
        try:
            file = await bot.get_file(action.file_id)
            buf = await bot.download_file(file.file_path)
        except Exception as exc:
            logger.warning("Очередь решений: не скачали чек %s: %s", action.action_id, exc)
            return _json({"error": "download_failed"}, status=502)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
        # тип файла — по расширению пути в Telegram: чек присылают и фото, и PDF-документом
        ctype = "image/jpeg" if action.file_type == "photo" else (
            mimetypes.guess_type(file.file_path or "")[0] or "application/octet-stream")
        return web.Response(body=data, content_type=ctype,
                            headers={"Cache-Control": "no-store"})

    async def inbox_decide(request: web.Request, user) -> web.Response:
        """approve — зачесть оплату / привязать ребёнка; reject — закрыть с отказом."""
        if pending_repo is None:
            return _json({"error": "unavailable"}, status=503)
        action = await pending_repo.get_by_id(request.match_info["aid"])
        if action is None or not await _allowed(user, action):
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        approve = bool(body.get("approve"))
        force = bool(body.get("force"))            # согласие зачесть сумму больше остатка
        if action.status != OPEN:
            return _json({"error": "already_decided", "status": action.status}, status=409)
        if action.kind == KIND_NEWCHILD:          # карточки ещё нет — заводим по заявке родителя
            notifier = (getattr(dp, "workflow_data", dp)).get("notifier")
            if approve:
                student = await approve_new_child(pending_repo, student_repo, dp["student_group_repo"],
                                                  notifier, action, user.tg_id)
                if student is None:
                    return _json({"error": "already_decided"}, status=409)
                return _json({"ok": True, "status": DONE, "studentId": student.student_id})
            if not await reject_new_child(pending_repo, notifier, action, user.tg_id):
                return _json({"error": "already_decided"}, status=409)
            return _json({"ok": True, "status": REJECTED})
        student = await student_repo.get_by_id(action.student_id)
        if student is None:
            return _json({"error": "not_found"}, status=404)

        if scope is not None and action.kind == KIND_CASH:
            # педагог наличные не зачитывает: «деньги у меня» → заявка ждёт администратора
            if action.held_by:
                return _json({"error": "held", "message": "Деньги у педагога — зачтёт администратор"}, status=409)
            if approve:
                if not await pending_repo.set_held_by(action.action_id, user.teacher_id):
                    return _json({"error": "already_decided"}, status=409)
                from bot.services import payment_events
                payment_events.cash_held(action, user.teacher_id)
                logger.info("Очередь решений: педагог %s — наличные %s у него", user.teacher_id, action.action_id)
                return _json({"ok": True, "status": "held"})

        if not approve:
            if not await pending_repo.claim(action.action_id, REJECTED, user.tg_id):
                return _json({"error": "already_decided"}, status=409)
            await _notify_parent(action, student, approved=False)
            return _json({"ok": True, "status": REJECTED})

        if action.kind == KIND_CHILD:
            addr = parse_addr(action.parent_addr)
            if addr is None:
                return _json({"error": "bad_request"}, status=400)
            if not await pending_repo.claim(action.action_id, DONE, user.tg_id):
                return _json({"error": "already_decided"}, status=409)
            await student_repo.add_parent(action.student_id, addr)
            await _notify_parent(action, student, approved=True)
            logger.info("Очередь решений: админ %s привязал родителя %s к %s",
                        user.tg_id, action.parent_addr, action.student_id)
            return _json({"ok": True, "status": DONE})

        # сумма больше остатка — переплату проводим только по явному подтверждению;
        # остаток считаем по педагогам, за которых платил родитель (action.keys)
        ledgers = await payment_service.ledger_for(student, action.period_month)
        rest = rest_for_keys(ledgers, action.keys)
        raw_amount = body.get("amount")
        amount = action.amount if raw_amount is None else int(raw_amount)   # 0 — «закрыть без зачёта»
        if amount < 0:
            return _json({"error": "bad_request"}, status=400)
        if amount > rest and not force:
            return _json({"error": "overpay", "needsConfirm": True,
                          "amount": amount, "rest": rest}, status=409)

        if not await pending_repo.claim(action.action_id, DONE, user.tg_id):
            return _json({"error": "already_decided"}, status=409)
        if amount > 0:
            async with payment_lock(action.student_id, action.period_month):    # общий замок с ручной отметкой
                credited, rows = await payment_service.record_payment(
                    action.student_id, student.name, action.period_month, amount,
                    user.tg_id, action.keys or None, "из очереди решений", action.method or "",
                )
        else:                                   # оплату уже отметили вручную — заявку просто закрываем
            credited, rows = 0, 0
        await pending_repo.close_for_period(action.student_id, action.period_month, DONE, user.tg_id)
        await _notify_parent(action, student, approved=True, credited=credited)
        logger.info("Очередь решений: админ %s зачёл %d руб. — %s %s",
                    user.tg_id, credited, action.student_id, action.period_month)
        return _json({"ok": True, "status": DONE, "credited": credited, "rows": rows,
                      "overpaid": max(0, credited - rest)})

    async def _notify_parent(action, student, approved: bool, credited: int = 0) -> None:
        notifier = (getattr(dp, "workflow_data", dp)).get("notifier")
        addr = parse_addr(action.parent_addr) if action.parent_addr else None
        if notifier is not None and addr is None and approved and credited:
            # наличные принял педагог (заявка без родителя) — «оплата подтверждена» всем родителям ученика
            await notify_payment_confirmed(notifier, student, action.period_month, credited)
            return
        if notifier is None or addr is None:
            return
        if action.kind == KIND_CHILD:
            text = (f"✅ Заявка одобрена: вы привязаны к ученику {student.name}."
                    if approved else "❌ Администратор отклонил вашу заявку.")
        else:
            text = (f"✅ Оплата {credited or action.amount} руб. за {period_label(action.period_month)} "
                    f"({student.name}) подтверждена." if approved else
                    f"❌ Оплата за {period_label(action.period_month)} ({student.name}) не подтверждена. "
                    f"Проверьте сумму или свяжитесь со школой.")
        try:
            await notifier.send(addr, text)
        except Exception as exc:
            logger.warning("Очередь решений: родителю %s не ушло: %s", action.parent_addr, exc)

    routes = [
        ("GET", "/inbox", inbox),
        ("GET", "/inbox/{aid}/file", inbox_file),
        ("POST", "/inbox/{aid}/decide", inbox_decide),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, prefix + path, admin_only(handler))
