"""HTTP API для Telegram Mini App (личный кабинет родителя).

MVP-мост поверх существующих сервисов бота — без отдельного бэкенда:
- GET  /api/me/bills           — счета детей текущего родителя за последние месяцы
- POST /api/me/pay             — создать платёж ЮКассы за (student_id, period_month)

Авторизация: заголовок `Authorization: tma <initData>` — подпись Telegram
проверяется на каждый запрос (bot/utils/telegram_auth.py). Суммы всегда
считает сервер из занятий; перед платежом создаются PENDING-счета, поэтому
вебхук ЮКассы (/yookassa-webhook) после succeeded помечает их PAID в таблице —
оплаченные занятия сохраняются так же, как при оплате из бота.
"""
from __future__ import annotations

import logging

from aiohttp import web

from config.settings import settings
from bot.services import payment_ledger
from bot.utils.dates import last_periods
from bot.utils.telegram_auth import verify_init_data

logger = logging.getLogger(__name__)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}
_BILLS_MONTHS = 3


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_CORS_HEADERS)


def _auth_tg_id(request: web.Request) -> int | None:
    """tg_id из проверенного initData или None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        return None
    parsed = verify_init_data(header[4:], settings.bot_token)
    if not parsed or not isinstance(parsed.get("user"), dict):
        return None
    tg_id = parsed["user"].get("id")
    return tg_id if isinstance(tg_id, int) else None


def register_miniapp_api(app: web.Application, dp, bot=None) -> None:
    student_repo = dp["student_repo"]
    payment_repo = dp["payment_repo"]
    payment_service = dp["payment_service"]
    client_repo = dp["client_repo"]
    user_repo = dp["user_repo"]

    async def bills(request: web.Request) -> web.Response:
        tg_id = _auth_tg_id(request)
        if tg_id is None:
            return _json({"error": "unauthorized"}, status=401)

        students = await student_repo.get_by_parent_tg_id(tg_id)
        periods = last_periods(_BILLS_MONTHS)
        out = []
        for student in students:
            for period in periods:
                bill_map = await payment_service.compute_bills_for_student_period(
                    student.student_id, period,
                )
                if not bill_map:
                    continue
                paid_sums = payment_ledger.paid_sums(
                    await payment_repo.get_by_student_and_period(student.student_id, period),
                )
                teachers = []
                to_pay = 0
                for teacher_id, agg in bill_map.items():
                    paid_amount = paid_sums.get(teacher_id, 0)
                    remainder = max(agg["total"] - paid_amount, 0)
                    to_pay += remainder
                    teachers.append({
                        "teacherId": teacher_id,
                        "teacherName": agg["name"],
                        "total": agg["total"],
                        "paid": paid_amount,
                        "toPay": remainder,
                        "status": "PAID" if remainder == 0 else ("PARTIAL" if paid_amount else "UNPAID"),
                    })
                out.append({
                    "studentId": student.student_id,
                    "studentName": student.name,
                    "period": period,
                    "teachers": teachers,
                    "total": sum(agg["total"] for agg in bill_map.values()),
                    "toPay": to_pay,
                })
        return _json({"bills": out})

    async def pay(request: web.Request) -> web.Response:
        tg_id = _auth_tg_id(request)
        if tg_id is None:
            return _json({"error": "unauthorized"}, status=401)
        if not (settings.yookassa_shop_id and settings.yookassa_secret_key):
            return _json({"error": "payments_disabled"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        student_id = body.get("studentId")
        period_month = body.get("periodMonth")
        if not isinstance(student_id, str) or not isinstance(period_month, str):
            return _json({"error": "bad_request"}, status=400)

        students = await student_repo.get_by_parent_tg_id(tg_id)
        student = next((s for s in students if s.student_id == student_id), None)
        if student is None:
            return _json({"error": "forbidden"}, status=403)

        # Как в боте: сумма — только неоплаченные педагоги; PENDING-счета
        # создаются заранее, чтобы вебхук ЮКассы после succeeded пометил их PAID.
        bill_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
        await payment_service.get_or_create_invoices_for_student_period(student, period_month)
        paid_sums = payment_ledger.paid_sums(
            await payment_repo.get_by_student_and_period(student_id, period_month),
        )
        total = sum(max(agg["total"] - paid_sums.get(tid, 0), 0) for tid, agg in bill_map.items())
        if total <= 0:
            return _json({"error": "nothing_to_pay"}, status=409)

        phone = email = ""
        if student.client_id:
            client = await client_repo.get_by_id(student.client_id)
            if client:
                phone, email = client.phone or "", client.email or ""
        try:
            url, payment_id = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total,
                customer_phone=phone, customer_email=email,
            )
            if bot is not None:
                from bot.services.payment_watcher import start_payment_watch
                start_payment_watch(
                    payment_id, student.student_id, student.name, period_month,
                    payment_service, bot, user_repo, parent_addr=("tg", tg_id),
                )
        except Exception as exc:
            logger.error("Mini App: ошибка создания платежа ЮКасса: %s", exc)
            return _json({"error": "payment_failed"}, status=502)

        logger.info(
            "Mini App: платёж создан tg_id=%s student=%s period=%s amount=%s",
            tg_id, student_id, period_month, total,
        )
        return _json({"confirmationUrl": url, "amount": total})

    async def options(_request: web.Request) -> web.Response:
        return web.Response(status=204, headers=_CORS_HEADERS)

    app.router.add_get("/api/me/bills", bills)
    app.router.add_post("/api/me/pay", pay)
    app.router.add_options("/api/me/bills", options)
    app.router.add_options("/api/me/pay", options)
    logger.info("Mini App API зарегистрирован: /api/me/bills, /api/me/pay")
