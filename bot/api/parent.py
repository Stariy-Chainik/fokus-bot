"""HTTP API кабинета родителя Telegram Mini App — /api/parent/*.

Тонкий слой над теми же сервисами, что и бот: суммы и остатки считает
`PaymentService` (`ledger_for`, `student_lesson_marks`), дневник — `DiaryService`,
поэтому кабинет, бот и MAX всегда показывают одно и то же. Родитель видит только
своих детей (`students.parent_tg_ids`).

Оплата: онлайн через ЮКассу (ссылка открывается прямо из кабинета) и «наличные»
(уведомление администратору, как в боте). Реквизиты и СБП показываются текстом —
чек по ним родитель присылает в бот, там его подтверждает администратор.

Авторизация — `Authorization: tma <initData>`; для локальной разработки `dev`.
"""
from __future__ import annotations

import logging
from base64 import b64encode

from aiohttp import web

from bot.api.admin import auth_tg_id
from bot.services import payment_ledger
from bot.services.diary_service import place_icon
from bot.screens.adapters import to_aiogram_markup
from bot.services.parent_views import (
    admin_confirm_rows, cash_notice, cash_options, client_contact, history_hidden, qr_png, visible_periods,
)
from bot.services.payment_methods import CASH
from bot.services.pending_queue import KIND_CASH, queue_action
from bot.utils.dates import current_period
from bot.utils.notify import notify
from config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "/api/parent"


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _visible_periods(count: int) -> list:
    """Месяцы для родителя: не раньше PARENT_BILLS_SINCE_PERIOD (до него — архив школы)."""
    return visible_periods(count)


def _hidden(period: str) -> bool:
    return history_hidden(period)


def _dp_get(dp, key: str):
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


def register_parent_api(app: web.Application, dp, bot=None) -> None:
    student_repo = dp["student_repo"]
    payment_repo = dp["payment_repo"]
    teacher_repo = dp["teacher_repo"]
    group_repo = dp["group_repo"]
    user_repo = dp["user_repo"]
    client_repo = dp["client_repo"]
    payment_service = dp["payment_service"]
    diary_service = _dp_get(dp, "diary_service")

    def parent_only(handler):
        """Родитель = tg_id есть в students.parent_tg_ids хотя бы одного ученика."""
        async def wrapped(request: web.Request) -> web.Response:
            tg_id = auth_tg_id(request)
            if tg_id is None:
                return _json({"error": "unauthorized"}, status=401)
            children = await student_repo.get_by_parent_tg_id(tg_id)
            if not children:
                return _json({"error": "forbidden"}, status=403)
            try:
                return await handler(request, tg_id, children)
            except web.HTTPException:
                raise
            except Exception as exc:
                logger.exception("Parent API %s: %s", request.path, exc)
                return _json({"error": "internal"}, status=500)
        return wrapped

    def _child(children, sid: str):
        return next((s for s in children if s.student_id == sid), None)

    async def _totals(student, period: str) -> dict:
        ledgers = await payment_service.ledger_for(student, period)
        accrued, paid, rest = payment_ledger.ledger_totals(ledgers)
        return {"accrued": accrued, "paid": paid, "rest": rest}

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def me(request: web.Request, tg_id, children) -> web.Response:
        student_group_repo = dp["student_group_repo"]
        name = ""
        for s in children:                               # имя родителя — из карточки клиента школы
            if s.client_id:
                client = await client_repo.get_by_id(s.client_id)
                if client and client.name:
                    name = client.name
                    break
        return _json({
            "tgId": tg_id, "name": name, "period": current_period(),
            "historySince": settings.parent_bills_since_period,   # раньше этого месяца экранов нет
            "children": [{
                "id": s.student_id, "name": s.name,
                # наличные: где-то приняты и предпочтительны, где-то не принимаются вовсе
                **dict(zip(("cashAllowed", "cashPreferred"),
                           await cash_options(s.student_id, student_group_repo), strict=False)),
            } for s in children],
            "methods": {
                "yookassa": bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
                "cash": bool(settings.payment_cash_enabled),
                "bank": bool(settings.payment_bank_details),
                "sbp": bool(settings.payment_sbp_details),
            },
        })

    async def home(request: web.Request, tg_id, children) -> web.Response:
        period = current_period()
        kids, rest_total = [], 0
        for s in children:
            months = []
            for ym in _visible_periods(3):
                t = await _totals(s, ym)
                if t["accrued"] or t["paid"]:
                    months.append({"ym": ym, **t})
            rest = sum(m["rest"] for m in months)
            rest_total += rest
            kids.append({"id": s.student_id, "name": s.name, "rest": rest,
                         "thisMonth": next((m for m in months if m["ym"] == period), None),
                         "months": months})
        return _json({"period": period, "rest": rest_total, "children": kids})

    # ── счета ────────────────────────────────────────────────────────────
    async def bills(request: web.Request, tg_id, children) -> web.Response:
        sid = request.query.get("student") or ""
        student = _child(children, sid) or children[0]
        months = []
        for ym in _visible_periods(6):
            t = await _totals(student, ym)
            if t["accrued"] or t["paid"]:
                months.append({"ym": ym, **t})
        return _json({"student": {"id": student.student_id, "name": student.name}, "months": months})

    async def bill(request: web.Request, tg_id, children) -> web.Response:
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        period = request.match_info["ym"]
        if _hidden(period):
            return _json({"error": "not_found"}, status=404)
        ledgers = await payment_service.ledger_for(student, period)
        rows = []
        for key, ledger in sorted(ledgers.items(), key=lambda kv: kv[1].name):
            marks = payment_ledger.lesson_marks(
                ledger.items, ledger.paid,
                payment_ledger.paid_lesson_ids(
                    await payment_repo.get_by_student_and_period(student.student_id, period),
                ).get(key, set()),
            )
            rows.append({
                "key": key, "name": ledger.name, "subscription": ledger.subscription,
                "accrued": ledger.accrued, "paid": ledger.paid, "rest": ledger.remainder,
                "overpaid": ledger.overpaid,
                "lessons": [{"id": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                             "amount": m["amount"], "paid": m["paid"],
                             "type": m.get("lesson_type") or ""}      # group | pair | soloist
                            for m in marks],
            })
        # Педагоги с прямой оплатой: в счёте видны как все (занятия и суммы),
        # но их строки не выбираются и в «К оплате» не входят — платят им лично.
        # Расчёт общий с ботом и MAX (`PaymentService.direct_pay_rows`).
        for direct in await payment_service.direct_pay_rows(student.student_id, period):
            rows.append({
                "key": f"DIRECT:{direct.teacher_id}", "name": direct.name, "subscription": False,
                "direct": True,                       # платит родитель лично педагогу
                "accrued": direct.total, "paid": 0, "rest": 0, "overpaid": 0,
                "lessons": [{"id": item.lesson_id, "date": item.date, "durationMin": item.duration_min,
                             "amount": item.amount, "paid": False, "type": "individual"}
                            for item in direct.lessons],
            })
        accrued, paid, rest = payment_ledger.ledger_totals(ledgers)
        return _json({
            "student": {"id": student.student_id, "name": student.name}, "period": period,
            "rows": rows, "accrued": accrued, "paid": paid, "rest": rest,
        })

    # ── занятия и дневник ────────────────────────────────────────────────
    async def lessons(request: web.Request, tg_id, children) -> web.Response:
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        period = request.query.get("ym") or current_period()
        if _hidden(period):
            return _json({"student": {"id": student.student_id, "name": student.name},
                          "period": period, "lessons": []})
        month = await payment_service.student_lesson_marks(student.student_id, period)
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}
        out = []
        for ls in month.lessons:                      # экран «Занятия» — расписание, без денег
            out.append({
                "id": ls.lesson_id, "date": ls.date, "durationMin": ls.duration_min,
                "type": ls.type.value, "teacherId": ls.teacher_id,
                "teacher": teachers.get(ls.teacher_id, ls.teacher_name),
                "group": groups.get(ls.group_id, ""),
            })
        return _json({
            "student": {"id": student.student_id, "name": student.name}, "period": period,
            "lessons": out,
        })

    async def diary(request: web.Request, tg_id, children) -> web.Response:
        if diary_service is None:
            return _json({"error": "unavailable"}, status=503)
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        period = request.query.get("ym") or current_period()
        if _hidden(period):
            return _json({"student": {"id": student.student_id, "name": student.name}, "period": period,
                          "athlete": bool(student.athlete_tg_id), "stats": {"sessions": 0, "minutes": 0, "points": 0,
                          "avgGrade": None, "byTopic": {}}, "place": None, "placeIcon": "", "entries": [], "openTasks": []})
        entries = await diary_service.entries_for_student(student.student_id, period=period)
        tasks = await diary_service.tasks_map(student.student_id)
        st = await diary_service.stats(student.student_id, period)
        board = await diary_service.leaderboard(period)
        row = next((r for r in board if r.student_id == student.student_id), None)
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        return _json({
            "student": {"id": student.student_id, "name": student.name}, "period": period,
            "athlete": bool(student.athlete_tg_id),      # кабинет спортсмена заведён — дневник ведётся
            "stats": {"sessions": st.sessions, "minutes": st.total_minutes, "points": st.points,
                      "avgGrade": st.avg_grade, "byTopic": st.by_topic},
            "place": row.place if row else None, "placeIcon": place_icon(row.place) if row else "",
            "entries": [{"id": e.entry_id, "date": e.date, "minutes": e.minutes,
                         "topics": list(e.topics or []), "comment": e.comment, "grade": e.grade,
                         "gradeComment": e.grade_comment,
                         "gradedBy": teachers.get(e.graded_by, ""),
                         "tasks": [tasks[t].exercise for t in (e.task_ids or []) if t in tasks]}
                        for e in sorted(entries, key=lambda e: e.date, reverse=True)],
            "openTasks": [{"exercise": t.exercise, "minutes": t.minutes, "comment": t.comment}
                          for t in await diary_service.open_tasks(student.student_id)],
        })

    # ── оплата ───────────────────────────────────────────────────────────
    async def pay(request: web.Request, tg_id, children) -> web.Response:
        """method=yookassa|ysbp — ссылка на оплату; cash — уведомление администратору.

        keys/lessonIds — что именно оплачивают: начисления и выбранные занятия.
        """
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        student = _child(children, (body or {}).get("studentId") or "")
        period = (body or {}).get("ym") or current_period()
        method = (body or {}).get("method") or "yookassa"
        keys = [k for k in ((body or {}).get("keys") or []) if isinstance(k, str)]
        lesson_ids = [x for x in ((body or {}).get("lessonIds") or []) if isinstance(x, str)]
        if student is None:
            return _json({"error": "not_found"}, status=404)

        ledgers = await payment_service.ledger_for(student, period)
        chosen = {k: v for k, v in ledgers.items() if not keys or k in keys}
        # плательщик мог отметить отдельные занятия: у такой позиции берём только их,
        # у остальных — весь остаток (абонемент всегда целиком)
        picked = set(lesson_ids)
        amount = 0
        for key, ledger in chosen.items():
            marks = payment_ledger.lesson_marks(ledger.items, ledger.paid)
            mine = [m for m in marks if m["lesson_id"] in picked and not m["paid"]]
            if mine:
                amount += sum(m["amount"] for m in mine)
                await payment_service.set_payment_intent(student, period, key, [m["lesson_id"] for m in mine])
            else:
                amount += ledger.remainder
                if ledger.pending and ledger.pending.lesson_ids:      # снимаем прошлое намерение
                    await payment_service.set_payment_intent(student, period, key, [])
        if amount <= 0:
            return _json({"error": "nothing_to_pay"}, status=409)

        breakdown = "\n".join(f"  • {v.name} — {v.remainder} руб." for v in chosen.values() if v.remainder)
        if method == "cash":
            if bot is None:
                return _json({"error": "bot_unavailable"}, status=503)
            # повторный тап (запрос к таблицам идёт несколько секунд) не должен слать
            # админам второе уведомление: одна открытая заявка на ученика и месяц
            pending_repo = _dp_get(dp, "pending_repo")
            if pending_repo is not None:
                try:
                    already = [a for a in await pending_repo.get_open()
                               if a.student_id == student.student_id and a.period_month == period
                               and a.kind == KIND_CASH]
                except Exception:
                    already = []
                if already:
                    logger.info("Кабинет родителя: наличные уже заявлены (%s) — повтор не шлём",
                                already[0].action_id)
                    return _json({"ok": True, "amount": already[0].amount,
                                  "notified": 0, "duplicate": True})
            # админу — те же кнопки, что из бота: подтверждает он одним нажатием,
            # частичная оплата зачитывается только на выбранные строки-остатки
            open_keys = [k for k, v in ledgers.items() if v.remainder > 0]
            partial = len(chosen) < len(open_keys) or amount < sum(v.remainder for v in chosen.values())
            pids = ".".join(str(v.pending_pid) for v in chosen.values() if v.pending_pid)
            action = await queue_action(pending_repo, KIND_CASH, student, period,
                                        amount=amount, method=CASH, parent_addr=str(tg_id))
            rows = admin_confirm_rows(student.student_id, period, pids, partial,
                                      ("tg", tg_id), amount, CASH,
                                      action_id=action.action_id if action else "")
            text = cash_notice(student.name, period, amount, breakdown)
            admins = [u.tg_id for u in await user_repo.get_admins()]
            await notify(bot, admins, text, reply_markup=to_aiogram_markup(rows))
            logger.info("Кабинет родителя: наличные %s ₽ — %s %s", amount, student.student_id, period)
            return _json({"ok": True, "amount": amount, "notified": len(admins)})

        if method in ("yookassa", "ysbp"):
            if not (settings.yookassa_shop_id and settings.yookassa_secret_key):
                return _json({"error": "payments_disabled"}, status=503)
            await payment_service.get_or_create_invoices_for_student_period(student, period)
            phone, email = await client_contact(student, client_repo)
            try:
                url, payment_id = await payment_service.create_yookassa_payment(
                    student.student_id, student.name, period, amount, sbp=(method == "ysbp"),
                    customer_phone=phone, customer_email=email,
                    teacher_ids=list(chosen) if keys else None,
                    lesson_ids=lesson_ids or None,
                )
            except Exception as exc:
                logger.error("Кабинет родителя: ошибка платежа ЮКасса: %s", exc)
                return _json({"error": "payment_failed"}, status=502)
            if bot is not None:
                from bot.services.payment_watcher import start_payment_watch
                start_payment_watch(payment_id, student.student_id, student.name, period,
                                    payment_service, bot, user_repo, parent_addr=("tg", tg_id),
                                    teacher_ids=list(chosen) if keys else None)
            logger.info("Кабинет родителя: платёж %s ₽ — %s %s", amount, student.student_id, period)
            return _json({"url": url, "amount": amount})

        if method in ("bank", "sbp"):
            details = (settings.payment_bank_details if method == "bank" else settings.payment_sbp_details)
            qr = qr_png(student.name, period, amount) if method == "bank" else None
            return _json({"amount": amount, "details": details.replace("\\n", "\n"),
                          "qr": ("data:image/png;base64," + b64encode(qr).decode()) if qr else "",
                          "hint": "После перевода пришлите чек в бот — администратор подтвердит оплату."})
        return _json({"error": "bad_request", "message": "Неизвестный способ оплаты"}, status=400)

    routes = [
        ("GET", "/me", me), ("GET", "/home", home),
        ("GET", "/bills", bills), ("GET", "/bill/{sid}/{ym}", bill),
        ("GET", "/lessons/{sid}", lessons), ("GET", "/diary/{sid}", diary),
        ("POST", "/pay", pay),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, parent_only(handler))
    logger.info("Parent API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
