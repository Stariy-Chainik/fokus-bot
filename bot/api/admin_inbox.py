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

from aiohttp import web

from bot.repositories.pending_action_repo import DONE, KIND_CASH, KIND_CHILD, KIND_RECEIPT, REJECTED
from bot.services.parent_notifier import parse_addr
from bot.services.parent_views import METHOD_LABELS
from bot.utils.dates import period_label

logger = logging.getLogger(__name__)

KIND_LABEL = {
    KIND_CASH: "Оплата наличными",
    KIND_RECEIPT: "Чек об оплате",
    KIND_CHILD: "Заявка на привязку ребёнка",
}


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def register_inbox_routes(app: web.Application, dp, admin_only, prefix: str, bot=None) -> None:
    pending_repo = dp["pending_repo"] if "pending_repo" in getattr(dp, "workflow_data", dp) else None
    student_repo = dp["student_repo"]
    payment_service = dp["payment_service"]
    request_repo = (getattr(dp, "workflow_data", dp)).get("student_request_repo")

    def _action_dto(a, rest: int | None = None) -> dict:
        return {
            "id": a.action_id, "kind": a.kind, "title": KIND_LABEL.get(a.kind, a.kind),
            "studentId": a.student_id, "student": a.student_name,
            "period": a.period_month, "periodLabel": period_label(a.period_month) if a.period_month else "",
            "amount": a.amount, "method": METHOD_LABELS.get(a.method, a.method),
            "comment": a.comment, "createdAt": a.created_at,
            "hasFile": bool(a.file_id), "rest": rest,
        }

    async def inbox(request: web.Request, user) -> web.Response:
        items = []
        if pending_repo is not None:
            for a in sorted(await pending_repo.get_open(), key=lambda x: x.created_at):
                rest = None
                if a.period_month and a.kind in (KIND_CASH, KIND_RECEIPT):
                    student = await student_repo.get_by_id(a.student_id)
                    if student is not None:
                        ledgers = await payment_service.ledger_for(student, a.period_month)
                        rest = sum(v.remainder for v in ledgers.values())
                items.append(_action_dto(a, rest))
        requests = []
        if request_repo is not None:
            for r in await request_repo.get_pending():
                requests.append({
                    "id": r.request_id, "kind": "student_request",
                    "title": "Педагог просит завести ученика",
                    "student": r.student_name, "comment": f"от {r.teacher_name}",
                    "createdAt": r.created_at,
                })
        return _json({"items": items, "requests": requests,
                      "total": len(items) + len(requests)})

    async def inbox_file(request: web.Request, user) -> web.Response:
        """Чек картинкой: отдаём файл из Telegram, не раскрывая file_id."""
        if pending_repo is None or bot is None:
            return _json({"error": "unavailable"}, status=503)
        action = await pending_repo.get_by_id(request.match_info["aid"])
        if action is None or not action.file_id:
            return _json({"error": "not_found"}, status=404)
        try:
            file = await bot.get_file(action.file_id)
            buf = await bot.download_file(file.file_path)
        except Exception as exc:
            logger.warning("Очередь решений: не скачали чек %s: %s", action.action_id, exc)
            return _json({"error": "download_failed"}, status=502)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
        ctype = "image/jpeg" if action.file_type == "photo" else "application/octet-stream"
        return web.Response(body=data, content_type=ctype,
                            headers={"Cache-Control": "no-store"})

    async def inbox_decide(request: web.Request, user) -> web.Response:
        """approve — зачесть оплату / привязать ребёнка; reject — закрыть с отказом."""
        if pending_repo is None:
            return _json({"error": "unavailable"}, status=503)
        action = await pending_repo.get_by_id(request.match_info["aid"])
        if action is None:
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        approve = bool(body.get("approve"))
        student = await student_repo.get_by_id(action.student_id)
        if student is None:
            return _json({"error": "not_found"}, status=404)

        if not approve:
            await pending_repo.close(action.action_id, REJECTED, user.tg_id)
            await _notify_parent(action, student, approved=False)
            return _json({"ok": True, "status": REJECTED})

        if action.kind == KIND_CHILD:
            addr = parse_addr(action.parent_addr)
            if addr is None:
                return _json({"error": "bad_request"}, status=400)
            await student_repo.add_parent(action.student_id, addr)
            await pending_repo.close(action.action_id, DONE, user.tg_id)
            await _notify_parent(action, student, approved=True)
            logger.info("Очередь решений: админ %s привязал родителя %s к %s",
                        user.tg_id, action.parent_addr, action.student_id)
            return _json({"ok": True, "status": DONE})

        credited, rows = await payment_service.record_payment(
            action.student_id, student.name, action.period_month, action.amount,
            user.tg_id, None, "из очереди решений", action.method or "",
        )
        await pending_repo.close_for_period(action.student_id, action.period_month, DONE, user.tg_id)
        await _notify_parent(action, student, approved=True, credited=credited)
        logger.info("Очередь решений: админ %s зачёл %d руб. — %s %s",
                    user.tg_id, credited, action.student_id, action.period_month)
        return _json({"ok": True, "status": DONE, "credited": credited, "rows": rows})

    async def _notify_parent(action, student, approved: bool, credited: int = 0) -> None:
        notifier = (getattr(dp, "workflow_data", dp)).get("notifier")
        addr = parse_addr(action.parent_addr) if action.parent_addr else None
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
