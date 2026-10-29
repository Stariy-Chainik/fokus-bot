"""HTTP API кабинета родителя Telegram Mini App — /api/parent/*.

Тонкий слой над теми же сервисами, что и бот: суммы и остатки считает
`PaymentService` (`ledger_for`, `student_lesson_marks`), дневник — `DiaryService`,
поэтому кабинет, бот и MAX всегда показывают одно и то же. Родитель видит только
своих детей (`students.parent_tg_ids`; в мини-приложении MAX — `students.parent_max_ids`, вход
`Authorization: max <initData>`, решение владельца 14.10.2026).

Оплата: онлайн через ЮКассу (ссылка открывается прямо из кабинета) и «наличные»
(уведомление администратору, как в боте). Реквизиты и СБП показываются текстом —
чек по ним родитель присылает в бот, там его подтверждает администратор.

Авторизация — `Authorization: tma <initData>`; для локальной разработки `dev`.
"""
from __future__ import annotations

import asyncio
import logging
from base64 import b64encode
from datetime import date, timedelta

from aiohttp import web
from aiogram.types import BufferedInputFile

from bot.utils.attendees import student_duration
from bot.utils.groups import lesson_group_title
from bot.api.admin import auth_max_id, auth_tg_id, max_user_name
from bot.utils.bill_format import payment_purpose
from bot.services import activity, payment_ledger
from bot.api.homework import homework_title, short
from bot.services.diary_service import place_icon
from bot.services.kindergarten import clean_value, kindergarten_group_names
from bot.services import parent_unlink
from bot.services.parent_notifier import MAX, TG, fmt_addr
from bot.screens.adapters import to_aiogram_markup
from bot.services.parent_views import (
    breakdown_lines, receipt_caption, unpaid_for,
    admin_confirm_rows, cash_notice, cash_options, client_contact, history_hidden, qr_png, visible_periods,
)
from bot.services.payment_methods import CASH
from bot.repositories.pending_action_repo import KIND_RECEIPT
from bot.services.pending_queue import KIND_CASH, open_receipt, queue_action, recent_online_payment
from bot.utils.dates import current_period, display_period, period_label
from bot.utils.notify import notify
from config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "/api/parent"
_RECEIPT_MAX_BYTES = 15 * 1024 * 1024     # чек с телефона — до 15 МБ


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


_pay_locks: dict[str, "asyncio.Lock"] = {}       # ученик → замок оплаты (двойное нажатие)


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
        """Родитель = его адрес есть у ученика: Telegram (`parent_tg_ids`) или MAX (`parent_max_ids`).
        Обработчик получает адрес `("tg"|"max", id)` — по нему заявки, уведомления, «кто привязан», отвязка."""
        async def wrapped(request: web.Request) -> web.Response:
            tg_id = auth_tg_id(request)
            max_id = auth_max_id(request) if tg_id is None else None
            if tg_id is None and max_id is None:
                return _json({"error": "unauthorized"}, status=401)
            addr = (TG, tg_id) if tg_id is not None else (MAX, max_id)
            children = await student_repo.get_by_parent(addr)
            if not children:
                return _json({"error": "forbidden"}, status=403)
            try:
                return await handler(request, addr, children)
            except web.HTTPException:
                raise
            except Exception as exc:
                logger.exception("Parent API %s: %s", request.path, exc)
                return _json({"error": "internal"}, status=500)
        return wrapped

    def _return_url(addr) -> str:
        """После оплаты ЮКассы — обратно в тот мессенджер, откуда платили (MAX — в чат бота MAX)."""
        if addr[0] != MAX:
            return ""
        from bot.max.render import ME
        return f"https://max.ru/{ME['username']}" if ME.get("username") else ""

    def _child(children, sid: str):
        return next((s for s in children if s.student_id == sid), None)

    async def _totals(student, period: str) -> dict:
        # только посчитать: строку-остаток пересчитывает сама оплата (pay), а запись на каждом открытии сводки
        # стоила 0,3–0,5 с на начисление и лишние запросы к таблице
        ledgers = await payment_service.ledger_for(student, period, sync=False)
        accrued, paid, rest = payment_ledger.ledger_totals(ledgers)
        return {"accrued": accrued, "paid": paid, "rest": rest}

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def _kg(children) -> dict:
        """student_id → садовые группы школы, где ребёнок занимается (пусто — не садовый)."""
        groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        return {s.student_id: await kindergarten_group_names(s.student_id, dp["student_group_repo"], groups)
                for s in children}

    async def me(request: web.Request, addr, children) -> web.Response:
        student_group_repo = dp["student_group_repo"]
        kg = await _kg(children)
        name = ""
        for s in children:                               # имя родителя — из карточки клиента школы
            if s.client_id:
                client = await client_repo.get_by_id(s.client_id)
                if client and client.name:
                    name = client.name
                    break
        return _json({
            "tgId": addr[1] if addr[0] == TG else None, "platform": addr[0], "name": name, "period": current_period(),
            "historySince": settings.parent_bills_since_period,   # раньше этого месяца экранов нет
            "children": [{
                "id": s.student_id, "name": s.name,
                "kindergarten": bool(kg[s.student_id]), "kgroup": s.kindergarten_group,
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

    async def home(request: web.Request, addr, children) -> web.Response:
        """Сводка «Мои дети»: требует внимания (оплатить, ждёт подтверждения, новые оценки) →
        занятия (месяц и сегодня) → месяц по ребёнку (начислено / оплачено)."""
        period = current_period()
        today = date.today().isoformat()
        # заявленные наличные и присланные чеки, которые администратор ещё не подтвердил
        pending_repo = _dp_get(dp, "pending_repo")
        open_actions: list = []
        if pending_repo is not None:
            try:
                open_actions = [a for a in await pending_repo.get_open() if a.kind in (KIND_CASH, KIND_RECEIPT)]
            except Exception as exc:                       # сводка важнее очереди
                logger.warning("Кабинет родителя: очередь решений недоступна: %s", exc)
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}
        kids, rest_total = [], 0
        kg = await _kg(children)
        hw_numbers = await diary_service.homework_numbers() if diary_service is not None else {}
        for s in children:
            months = []
            for ym in _visible_periods(3):
                t = await _totals(s, ym)
                if t["accrued"] or t["paid"]:
                    months.append({"ym": ym, **t})
            rest = sum(m["rest"] for m in months)
            rest_total += rest
            month_lessons = [] if _hidden(period) else (
                await payment_service.student_lesson_marks(s.student_id, period)).lessons
            grades = []
            if diary_service is not None and s.is_athlete:      # оценки педагога за последнюю неделю
                for e in await diary_service.entries_for_student(s.student_id, days=7):
                    if e.grade:
                        grades.append({"date": e.date, "grade": e.grade, "topics": list(e.topics or []),
                                       "teacher": teachers.get(e.graded_by, ""), "comment": e.grade_comment or ""})
            if diary_service is not None:                       # оценки за домашние задания — всем детям
                week_ago = (date.today() - timedelta(days=7)).isoformat()
                for t in await diary_service.student_tasks(s.student_id):
                    if t.grade and t.graded_at[:10] >= week_ago:
                        title = homework_title(hw_numbers.get(t.batch_id, 0), t.created_at)
                        grades.append({"date": t.graded_at[:10], "grade": t.grade,
                                       "topics": [f"{title}: {short(t.exercise)}"],
                                       "teacher": teachers.get(t.teacher_id, ""), "comment": t.grade_comment,
                                       "homework": True, "scale": settings.homework_scale(t.teacher_id)})
            grades.sort(key=lambda g: g["date"], reverse=True)
            kids.append({
                "id": s.student_id, "name": s.name, "rest": rest,
                "thisMonth": next((m for m in months if m["ym"] == period), None),
                "months": months,
                "unpaid": [m for m in months if m["rest"]],              # закрытые месяцы первыми
                "pending": [{"kind": a.kind, "amount": a.amount, "ym": a.period_month, "method": a.method}
                            for a in open_actions if a.student_id == s.student_id],
                "grades": grades,
                "parents": len(s.parent_addrs),                            # «Кто привязан» — p.family
                # группа в детском саду: родитель указывает сам (решение владельца 10.10.2026)
                "kindergarten": bool(kg[s.student_id]), "kgroup": s.kindergarten_group,
                "lessons": {
                    "month": len(month_lessons),
                    "minutes": sum(student_duration(ls, s.student_id) for ls in month_lessons),   # своя длительность (сад: 35)
                    "last": max((ls.date for ls in month_lessons), default=None),
                    "today": [{"type": ls.type.value, "durationMin": student_duration(ls, s.student_id),
                               "teacher": teachers.get(ls.teacher_id, ls.teacher_name),
                               "group": lesson_group_title(ls, groups)}
                              for ls in month_lessons if ls.date == today],
                },
            })
        return _json({"period": period, "today": today, "rest": rest_total, "children": kids})

    # ── счета ────────────────────────────────────────────────────────────
    async def bills(request: web.Request, addr, children) -> web.Response:
        sid = request.query.get("student") or ""
        student = _child(children, sid) or children[0]
        months = []
        for ym in _visible_periods(6):
            t = await _totals(student, ym)
            if t["accrued"] or t["paid"]:
                months.append({"ym": ym, **t})
        return _json({"student": {"id": student.student_id, "name": student.name}, "months": months})

    async def bill(request: web.Request, addr, children) -> web.Response:
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        period = request.match_info["ym"]
        if _hidden(period):
            return _json({"error": "not_found"}, status=404)
        ledgers = await payment_service.ledger_for(student, period, sync=False)    # просмотр — без записи остатка
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
    async def lessons(request: web.Request, addr, children) -> web.Response:
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
                "id": ls.lesson_id, "date": ls.date, "durationMin": student_duration(ls, student.student_id),
                "type": ls.type.value, "teacherId": ls.teacher_id,
                "teacher": teachers.get(ls.teacher_id, ls.teacher_name),
                "group": lesson_group_title(ls, groups),
            })
        return _json({
            "student": {"id": student.student_id, "name": student.name}, "period": period,
            "lessons": out,
        })

    async def diary(request: web.Request, addr, children) -> web.Response:
        if diary_service is None:
            return _json({"error": "unavailable"}, status=503)
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        period = request.query.get("ym") or current_period()
        if _hidden(period):
            return _json({"student": {"id": student.student_id, "name": student.name}, "period": period,
                          "athlete": student.is_athlete, "stats": {"sessions": 0, "minutes": 0, "points": 0,
                          "avgGrade": None, "byTopic": {}}, "place": None, "placeIcon": "", "entries": [], "openTasks": []})
        entries = await diary_service.entries_for_student(student.student_id, period=period)
        tasks = await diary_service.tasks_map(student.student_id)
        st = await diary_service.stats(student.student_id, period)
        board = await diary_service.leaderboard(period)
        row = next((r for r in board if r.student_id == student.student_id), None)
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        hw_numbers = await diary_service.homework_numbers()
        return _json({
            "student": {"id": student.student_id, "name": student.name}, "period": period,
            "athlete": student.is_athlete,      # кабинет спортсмена заведён — дневник ведётся
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
            # все задания педагогов (домашние группе и спортсмену) с оценкой за выполнение — 15.10.2026
            "homework": [{"id": t.task_id, "exercise": t.exercise, "minutes": t.minutes, "comment": t.comment,
                          "teacher": teachers.get(t.teacher_id, ""), "createdAt": t.created_at, "status": t.status,
                          "grade": t.grade, "gradeComment": t.grade_comment, "gradedAt": t.graded_at,
                          "number": hw_numbers.get(t.batch_id, 0), "scale": settings.homework_scale(t.teacher_id)}
                         for t in (await diary_service.student_tasks(student.student_id))[:30]],
        })

    # ── оплата ───────────────────────────────────────────────────────────
    async def pay_months(student, periods: list, method: str, addr) -> web.Response:
        """«Оплатить всё»: наличные или реквизиты сразу за несколько месяцев (весь остаток каждого).

        Зачёт идёт по месяцам, поэтому наличные — по заявке на месяц (администратору по сообщению на
        месяц), реквизиты — общая сумма и общее назначение платежа; чек потом прикрепляется один раз
        за все месяцы (`/receipt` с `periods`)."""
        due = {}
        for ym in sorted(set(periods)):
            ledgers = await payment_service.ledger_for(student, ym)
            amount = sum(v.remainder for v in ledgers.values())
            if amount > 0:
                due[ym] = (amount, ledgers)
        if not due:
            return _json({"error": "nothing_to_pay"}, status=409)
        total = sum(a for a, _ in due.values())
        if method in ("yookassa", "ysbp"):                # один платёж ЮКассы за все месяцы
            if not (settings.yookassa_shop_id and settings.yookassa_secret_key):
                return _json({"error": "payments_disabled"}, status=503)
            phone, email = await client_contact(student, client_repo)
            try:
                url, payment_id = await payment_service.create_yookassa_payment(
                    student.student_id, student.name, sorted(due)[0], total, sbp=(method == "ysbp"),
                    customer_phone=phone, customer_email=email, return_url=_return_url(addr), periods=sorted(due),
                )
            except Exception as exc:
                logger.error("Кабинет родителя: ошибка платежа ЮКасса за %d мес.: %s", len(due), exc)
                return _json({"error": "payment_failed"}, status=502)
            if bot is not None:
                from bot.services.payment_watcher import start_payment_watch
                start_payment_watch(payment_id, student.student_id, student.name, sorted(due)[0],
                                    payment_service, bot, user_repo, parent_addr=addr, periods=sorted(due))
            logger.info("Кабинет родителя: платёж ЮКасса %s ₽ за %d мес. — %s", total, len(due), student.student_id)
            return _json({"url": url, "amount": total, "months": len(due)})
        if method == "cash":
            if bot is None:
                return _json({"error": "bot_unavailable"}, status=503)
            pending_repo = _dp_get(dp, "pending_repo")
            admins = [u.tg_id for u in await user_repo.get_admins()]
            notified = new = 0
            for ym, (amount, ledgers) in due.items():
                try:
                    open_cash = [a for a in await pending_repo.get_open() if a.student_id == student.student_id
                                 and a.period_month == ym and a.kind == KIND_CASH] if pending_repo is not None else []
                except Exception:
                    open_cash = []
                if open_cash:
                    continue                                      # за этот месяц уже заявлено
                breakdown = "\n".join(f"  • {v.name} — {v.remainder} руб." for v in ledgers.values() if v.remainder)
                pids = ".".join(str(v.pending_pid) for v in ledgers.values() if v.pending_pid)
                action = await queue_action(pending_repo, KIND_CASH, student, ym, amount=amount, method=CASH,
                                            parent_addr=fmt_addr(addr))
                rows = admin_confirm_rows(student.student_id, ym, pids, False, addr, amount, CASH,
                                          action_id=action.action_id if action else "")
                await notify(bot, admins, cash_notice(student.name, ym, amount, breakdown),
                             reply_markup=to_aiogram_markup(rows))
                notified += len(admins)
                new += 1
            logger.info("Кабинет родителя: наличные за %d мес., %s ₽ — %s", len(due), total, student.student_id)
            return _json({"ok": True, "amount": total, "months": len(due), "notified": notified,
                          "duplicate": new == 0})
        details = settings.payment_bank_details if method == "bank" else settings.payment_sbp_details
        purpose = f"Оплата занятий, {student.name.strip()}, " + ", ".join(display_period(ym) for ym in due)
        return _json({"amount": total, "months": len(due), "periods": list(due), "qr": "",
                      "details": details.replace("\\n", "\n") + f"\n\nНазначение платежа (скопируйте):\n{purpose}",
                      "hint": "После перевода прикрепите один чек — он будет отправлен за все месяцы."})

    async def pay(request: web.Request, addr, children) -> web.Response:
        """method=yookassa|ysbp — ссылка на оплату; cash — уведомление администратору.

        keys/lessonIds — что именно оплачивают: начисления и выбранные занятия.
        Один ученик — одна оплата за раз: двойное нажатие «наличными» иначе создавало две заявки.
        """
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        sid = str((body or {}).get("studentId") or "")
        lock = _pay_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            return await _pay(body, addr, children)

    async def _pay(body, addr, children) -> web.Response:
        student = _child(children, (body or {}).get("studentId") or "")
        period = (body or {}).get("ym") or current_period()
        method = (body or {}).get("method") or "yookassa"
        keys = [k for k in ((body or {}).get("keys") or []) if isinstance(k, str)]
        lesson_ids = [x for x in ((body or {}).get("lessonIds") or []) if isinstance(x, str)]
        if student is None:
            return _json({"error": "not_found"}, status=404)
        periods = [x for x in ((body or {}).get("periods") or []) if isinstance(x, str) and not _hidden(x)]
        if len(periods) >= 2 and method in ("cash", "bank", "sbp", "ysbp", "yookassa"):
            return await pay_months(student, periods, method, addr)

        ledgers = await payment_service.ledger_for(student, period)
        chosen = {k: v for k, v in ledgers.items() if not keys or k in keys}
        # плательщик мог отметить отдельные занятия: у такой позиции берём только их,
        # у остальных — весь остаток (абонемент всегда целиком)
        picked = set(lesson_ids)
        amount = 0
        for key, ledger in chosen.items():
            marks = payment_ledger.lesson_marks(ledger.items, ledger.paid, payment_ledger.linked_ids(ledger.paid_rows))
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
                                        amount=amount, method=CASH, parent_addr=fmt_addr(addr),
                                        teacher_keys=list(chosen) if partial else None)
            rows = admin_confirm_rows(student.student_id, period, pids, partial,
                                      addr, amount, CASH,
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
                    customer_phone=phone, customer_email=email, return_url=_return_url(addr),
                    teacher_ids=list(chosen) if keys else None,
                    lesson_ids=lesson_ids or None,
                )
            except Exception as exc:
                logger.error("Кабинет родителя: ошибка платежа ЮКасса: %s", exc)
                return _json({"error": "payment_failed"}, status=502)
            if bot is not None:
                from bot.services.payment_watcher import start_payment_watch
                start_payment_watch(payment_id, student.student_id, student.name, period,
                                    payment_service, bot, user_repo, parent_addr=addr,
                                    teacher_ids=list(chosen) if keys else None)
            logger.info("Кабинет родителя: платёж %s ₽ — %s %s", amount, student.student_id, period)
            return _json({"url": url, "amount": amount})

        if method in ("bank", "sbp"):
            details = (settings.payment_bank_details if method == "bank" else settings.payment_sbp_details)
            qr = qr_png(student.name, period, amount) if method == "bank" else None
            details = details.replace("\\n", "\n") + f"\n\nНазначение платежа (скопируйте):\n{payment_purpose(student.name, period)}"
            return _json({"amount": amount, "details": details,
                          "qr": ("data:image/png;base64," + b64encode(qr).decode()) if qr else "",
                          "hint": "После перевода прикрепите чек кнопкой ниже — администратор подтвердит оплату."})
        return _json({"error": "bad_request", "message": "Неизвестный способ оплаты"}, status=400)

    async def receipt(request: web.Request, addr, children) -> web.Response:
        """Чек об оплате по реквизитам/СБП из кабинета: multipart (studentId, ym, method, amount, file).

        Файл уходит администраторам в Telegram с теми же кнопками, что чек из бота, и ставится
        в очередь решений с file_id — в кабинете администратора чек виден картинкой.
        `periods` («2026-09,2026-10») — один чек за несколько месяцев сразу: на каждый месяц своя заявка
        на его остаток (оплата зачитывается по месяцам), администратору — по сообщению на месяц.
        """
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        try:
            form = await request.post()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        student = _child(children, str(form.get("studentId") or ""))
        period = str(form.get("ym") or current_period())
        method = str(form.get("method") or "bank")
        upload = form.get("file")
        if student is None or method not in ("bank", "sbp") or not hasattr(upload, "file"):
            return _json({"error": "bad_request"}, status=400)
        data = upload.file.read()
        if not data or len(data) > _RECEIPT_MAX_BYTES:
            return _json({"error": "file_too_big" if data else "bad_request"}, status=400)
        ctype = (upload.content_type or "").lower()
        is_image = ctype.startswith("image/")
        if not is_image and ctype != "application/pdf":
            return _json({"error": "bad_file_type"}, status=400)
        try:
            amount = int(str(form.get("amount") or 0))
        except ValueError:
            amount = 0
        filename = upload.filename or ("receipt.jpg" if is_image else "receipt.pdf")
        if not str(form.get("force") or ""):
            online = await recent_online_payment(payment_repo, student.student_id)
            if online is not None:                      # чек ЮКассы после СБП онлайн — платёж уже зачтён
                return _json({"error": "online_recent", "amount": online.total_amount, "ym": online.period_month,
                              "periodLabel": period_label(online.period_month)}, status=409)
        admins = [u.tg_id for u in await user_repo.get_admins()]

        async def submit(period: str, amount: int) -> dict:
            """Один месяц: заявка в очередь и сообщения админам. {"error"} — отказ, иначе итог для родителя."""
            pending_repo = _dp_get(dp, "pending_repo")
            if await open_receipt(pending_repo, student.student_id, period):
                return {"ok": True, "duplicate": True, "amount": amount, "notified": 0}
            if amount <= 0:
                amount, _ = await unpaid_for(student, period, payment_service)
            bills = await payment_service.compute_bills_for_student_period(student.student_id, period)
            ledgers = await payment_service.ledger_for(student, period)
            caption = receipt_caption(method, student.name, period, amount,
                                      "\n".join(breakdown_lines(bills, list(bills), ledgers=ledgers)))
            if not admins:
                return {"error": "bot_unavailable"}

            async def send(admin_id: int, markup):
                payload = BufferedInputFile(data, filename=filename)
                if is_image:
                    return await bot.send_photo(admin_id, payload, caption=caption, reply_markup=markup)
                return await bot.send_document(admin_id, payload, caption=caption, reply_markup=markup)

            # первому админу — без кнопок, чтобы получить file_id для очереди; затем кнопки по номеру решения
            first, file_id, file_type = None, "", "photo" if is_image else "document"
            try:
                first = await send(admins[0], None)
                file_id = (first.photo[-1].file_id if is_image else first.document.file_id) if first else ""
            except Exception as exc:
                logger.warning("Кабинет родителя: чек не ушёл админу %s: %s", admins[0], exc)
            action = await queue_action(pending_repo, KIND_RECEIPT, student, period, amount=amount,
                                        method=method, parent_addr=fmt_addr(addr), file_id=file_id, file_type=file_type)
            rows = admin_confirm_rows(student.student_id, period, "", False, addr, amount, method,
                                      action_id=action.action_id if action else "")
            markup = to_aiogram_markup(rows)
            sent = 0
            if first is not None:
                try:
                    await bot.edit_message_reply_markup(chat_id=admins[0], message_id=first.message_id, reply_markup=markup)
                    sent += 1
                except Exception as exc:
                    logger.warning("Кабинет родителя: кнопки к чеку не добавились: %s", exc)
            for admin_id in admins[1:]:
                try:
                    await send(admin_id, markup)
                    sent += 1
                except Exception as exc:
                    logger.warning("Кабинет родителя: чек не ушёл админу %s: %s", admin_id, exc)
            logger.info("Кабинет родителя: чек %s ₽ — %s %s (%s), админов: %d", amount, student.student_id, period, method, sent)
            return {"ok": True, "amount": amount, "notified": sent}

        periods = [x for x in str(form.get("periods") or "").split(",") if x and not _hidden(x)]
        if len(periods) >= 2:
            results = []
            for ym in periods:
                due, _ = await unpaid_for(student, ym, payment_service)
                if due > 0:
                    results.append(await submit(ym, due))
            if not results:
                return _json({"error": "nothing_to_pay"}, status=409)
            if any("error" in r for r in results):
                return _json({"error": "bot_unavailable"}, status=503)
            return _json({"ok": True, "months": len(results), "amount": sum(r["amount"] for r in results),
                          "notified": sum(r["notified"] for r in results),
                          "duplicate": all(r.get("duplicate") for r in results)})
        result = await submit(period, amount)
        if "error" in result:
            return _json({"error": result["error"]}, status=503)
        return _json(result)

    # ── кто привязан к ребёнку и заявка на отвязку лишних (общая логика с MAX — parent_unlink) ──
    async def family(request: web.Request, addr, children) -> web.Response:
        """Кто привязан к каждому ребёнку: имя и @ник (Telegram), мессенджер, когда привязан,
        ждёт ли заявка на отвязку. Чужие tg_id наружу не отдаём — строку адресует `key`."""
        return _json({"children": await parent_unlink.family_view(
            children, addr, bot, client_repo, _dp_get(dp, "pending_repo"), _dp_get(dp, "activity_repo"),
            settings.bot_token)})

    async def unlink_request(request: web.Request, addr, children) -> web.Response:
        """Попросить отвязать другого родителя: решает администратор (решение владельца 06.10.2026)."""
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        status, action = await parent_unlink.submit_unlink(
            student, addr, str(body.get("key") or ""), str(body.get("reason") or ""),
            tg_bot=bot, user_repo=user_repo, client_repo=client_repo, pending_repo=_dp_get(dp, "pending_repo"),
            activity_repo=_dp_get(dp, "activity_repo"), secret=settings.bot_token,
            # из MAX имени через Bot API не узнать — берём его из данных входа мини-приложения
            requester_info={"name": max_user_name(request).split(" @")[0], "username": ""} if addr[0] == MAX else None)
        if status == "ok":
            return _json({"ok": True, "id": action.action_id})
        if status == "already":
            return _json({"error": "already", "id": action.action_id}, status=409)
        return _json({"error": status}, status={"self": 400, "not_found": 404}.get(status, 503))

    async def child_kgroup(request: web.Request, addr, children) -> web.Response:
        """Родитель указывает группу ребёнка в детском саду — номер или название (решение владельца 10.10.2026)."""
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        if not (await _kg([student]))[student.student_id]:
            return _json({"error": "not_kindergarten", "message": "Ребёнок не в садовой группе"}, status=400)
        try:
            body = await request.json()
        except Exception:
            body = None
        value = clean_value((body or {}).get("value"))
        if value is None:
            return _json({"error": "too_long", "message": "Не длиннее 40 символов"}, status=400)
        await student_repo.update_kindergarten_group(student.student_id, value, actor=addr[1] if addr[0] == TG else 0,
                                                     who="родитель" if addr[0] == TG else "родитель (MAX)")
        return _json({"ok": True, "value": value})

    async def unlink(request: web.Request, addr, children) -> web.Response:
        """«Это не мой ребёнок»: родитель сам снимает ошибочную привязку (решение владельца 03.10.2026).
        Снимается только его адрес; администраторам — сообщение, чтобы заметить и ошибку, и злоупотребление."""
        student = _child(children, request.match_info["sid"])
        if student is None:
            return _json({"error": "not_found"}, status=404)
        if not await student_repo.remove_parent(student.student_id, addr):
            return _json({"error": "not_found"}, status=404)
        # адрес мог быть и в карточке клиента — по нему уходили бы счета чужого ребёнка
        await parent_unlink.drop_client_addr(student_repo, client_repo, student, addr)
        where = f"{'Telegram' if addr[0] == TG else 'MAX'} {addr[1]}"
        await activity.record(activity.STUDENT, f"Родитель отвязался сам: {student.student_id} · {where}",
                              actor=addr[1] if addr[0] == TG else 0, ref=student.student_id)
        if bot is not None:
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()],
                         f"↩️ Родитель отвязался от ученика\nУченик: {student.name}\n{where.replace(' ', ': ', 1)}\n"
                         f"Причина: «это не мой ребёнок» в кабинете. Если ошибка — привяжите заново ссылкой группы.")
        left = len(children) - 1
        logger.info("Кабинет родителя: %s отвязался от %s, осталось детей: %d", fmt_addr(addr), student.student_id, left)
        return _json({"ok": True, "left": left})

    routes = [
        ("GET", "/me", me), ("GET", "/home", home), ("DELETE", "/children/{sid}", unlink),
        ("GET", "/family", family), ("POST", "/children/{sid}/unlink-request", unlink_request),
        ("PUT", "/children/{sid}/kgroup", child_kgroup),
        ("GET", "/bills", bills), ("GET", "/bill/{sid}/{ym}", bill),
        ("GET", "/lessons/{sid}", lessons), ("GET", "/diary/{sid}", diary),
        ("POST", "/pay", pay), ("POST", "/receipt", receipt),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, parent_only(handler))
    logger.info("Parent API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
