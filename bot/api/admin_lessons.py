"""Кабинет администратора: занятия за день, карточка занятия, удаление (замок периода обходится, как в боте)."""
from __future__ import annotations

import logging
from datetime import date

from aiohttp import web

from bot.models.enums import LessonType
from bot.services.billing_service import build_billing_rows, calc_earned
from bot.api.record import RecordError, record_create, record_options
from bot.services.profit_service import is_owner, lesson_rent
from bot.utils.attendees import free_attendee_label, has_amount_snapshots, parse_attendees
from config.settings import settings

logger = logging.getLogger(__name__)


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def register_lesson_routes(app: web.Application, dp, guard, prefix: str) -> None:
    lesson_repo = dp["lesson_repo"]
    teacher_repo = dp["teacher_repo"]
    group_repo = dp["group_repo"]
    student_repo = dp["student_repo"]
    submission_repo = dp["submission_repo"]
    lesson_service = dp["lesson_service"]
    salary_service = dp["salary_service"]

    async def _economy(ls, t, g) -> dict | None:
        """Что школа заработала с занятия (решение владельца 06.10.2026): выручка с учеников
        (абонемент — доля месяца: сбор группы ÷ число занятий), зарплата педагога (смена — доля дня),
        аренда у прямой оплаты, прибыль. Пояснения — в `note`."""
        income = sum(r.amount for r in build_billing_rows(ls, t)) if t else 0
        note, kind = "", "lessons"
        ym = ls.date[:7]
        if g is not None and g.billing_mode.value == "subscription":
            # абонемент начисляется за месяц, а не за занятие — делить его по урокам владелец
            # не хочет (06.10.2026): экономика у таких занятий не показывается, только зарплата
            return None
        rent = lesson_rent(ls)
        if rent and not income:
            kind, note = "rent", "родители платят педагогу напрямую, школе — аренда зала"
            income = rent
        salary = 0
        if t:
            line = next((x for x in await salary_service.lines_for(t, ym) if x.lesson_id == ls.lesson_id), None)
            if line is not None and line.kind == "in_shift":
                day = sum(x.amount for x in await salary_service.lines_for(t, ym) if x.kind == "shift" and x.date == ls.date)
                groups_day = len({x.group_id for x in await lesson_repo.get_all()
                                  if x.teacher_id == t.teacher_id and x.date == ls.date and x.group_id})
                salary = round(day / groups_day) if groups_day else 0
                note = (note + " · " if note else "") + f"смена {day} ₽ за день ÷ {groups_day} групп"
            else:
                salary = line.amount if line is not None else 0
        owner = bool(t) and is_owner(t.teacher_id)
        return {"kind": kind, "income": income, "salary": 0 if owner else salary, "ownerIncome": salary if owner else 0,
                "rent": rent, "profit": income - (0 if owner else salary), "note": note}

    async def _locked(lesson) -> bool:
        if not settings.teacher_period_submit_enabled:      # сдача периода выключена — замков нет
            return False
        return await submission_repo.get_by_teacher_and_period(lesson.teacher_id, lesson.date[:7]) is not None

    def _slot_names(lesson) -> list[str]:
        return [n for n in (lesson.student_1_name, lesson.student_2_name,
                            getattr(lesson, "student_3_name", None), getattr(lesson, "student_4_name", None)) if n]

    async def lessons(request: web.Request, user) -> web.Response:
        """?date=YYYY-MM-DD — день (по умолчанию сегодня), ?ym=YYYY-MM — месяц; ?tid= — только этот педагог."""
        ym = request.query.get("ym") or ""
        day = "" if ym else (request.query.get("date") or date.today().isoformat())
        key = ym or day
        if len(key) not in (7, 10):
            return _json({"error": "bad_request"}, status=400)
        tid = request.query.get("tid") or ""
        teachers = {t.teacher_id: t for t in await teacher_repo.get_all()}
        groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        students = {s.student_id: s.name for s in await student_repo.get_all()}
        rows = [x for x in await lesson_repo.get_all()
                if (x.date[:7] == ym if ym else x.date == day) and (not tid or x.teacher_id == tid)]
        out = []
        for ls in sorted(rows, key=lambda x: (x.date, x.recorded_at or "")):
            t = teachers.get(ls.teacher_id)
            names = (_slot_names(ls) if ls.type != LessonType.GROUP
                     else [students.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees)])
            out.append({
                "id": ls.lesson_id, "date": ls.date, "teacherId": ls.teacher_id,
                "teacherName": t.name if t else ls.teacher_name, "type": ls.type.value,
                "groupName": groups[ls.group_id].name if ls.group_id in groups else "",
                "students": names, "durationMin": ls.duration_min,
                "earned": calc_earned(ls.type, ls.duration_min, t, ls.group_id, ls.attendees, ls.date) if t else 0,
                "rent": lesson_rent(ls),        # аренда зала у педагога с прямой оплатой (статистика, не оплата)
                "recordedAt": ls.recorded_at, "locked": await _locked(ls),
            })
        return _json({"date": day, "key": key, "isDay": bool(day), "lessons": out,
                      "earned": sum(x["earned"] for x in out), "rent": sum(x["rent"] for x in out)})

    async def lesson(request: web.Request, user) -> web.Response:
        ls = await lesson_repo.get_by_id(request.match_info["lid"])
        if ls is None:
            return _json({"error": "not_found"}, status=404)
        t = await teacher_repo.get_by_id(ls.teacher_id)
        g = await group_repo.get_by_id(ls.group_id) if ls.group_id else None
        students = {s.student_id: s.name for s in await student_repo.get_all()}
        if ls.type == LessonType.GROUP:
            attendees = [{"studentId": e.student_id, "name": students.get(e.student_id, e.student_id),
                          "durationMin": e.duration_min, "amount": e.amount} for e in parse_attendees(ls.attendees)]
        else:
            ids = [i for i in (ls.student_1_id, ls.student_2_id, getattr(ls, "student_3_id", None), getattr(ls, "student_4_id", None)) if i]
            attendees = [{"studentId": i, "name": students.get(i, n), "durationMin": ls.duration_min, "amount": None}
                         for i, n in zip(ids, _slot_names(ls), strict=False)]
        return _json({
            "id": ls.lesson_id, "date": ls.date, "type": ls.type.value, "durationMin": ls.duration_min,
            "teacherId": ls.teacher_id, "teacherName": t.name if t else ls.teacher_name,
            "groupId": ls.group_id or "", "groupName": g.name if g else "",
            "groupMode": g.billing_mode.value if g else "",
            "freeLabel": free_attendee_label(g, has_amount_snapshots(ls.attendees)),
            "attendees": attendees, "recordedAt": ls.recorded_at,
            "earned": calc_earned(ls.type, ls.duration_min, t, ls.group_id, ls.attendees, ls.date) if t else 0,
            "locked": await _locked(ls),
            "economy": await _economy(ls, t, g),
        })

    async def lesson_delete(request: web.Request, user) -> web.Response:
        lid = request.match_info["lid"]
        ok = await lesson_service.delete(lid, bypass_period_lock=True)
        if ok:
            logger.info("Mini App: админ %s удалил занятие %s", user.tg_id, lid)
        return _json({"ok": ok}, status=200 if ok else 404)

    # ── запись занятия за педагога (замок периода обходится, как у админа в боте) ──
    async def record_options_view(request: web.Request, user) -> web.Response:
        tid = request.query.get("teacher", "")
        if await teacher_repo.get_by_id(tid) is None:
            return _json({"error": "not_found"}, status=404)
        return _json(await record_options(dp, tid))

    async def record_create_view(request: web.Request, user) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return _json({"error": "bad_request"}, status=400)
        teacher = await teacher_repo.get_by_id(body.get("teacherId") or "")
        if teacher is None:
            return _json({"error": "not_found"}, status=404)
        try:
            result = await record_create(dp, teacher, body, bypass_period_lock=True)
        except RecordError as exc:
            return _json({"error": exc.code, "message": str(exc)}, status=exc.status)
        logger.info("Mini App: админ %s отметил занятие за %s: %s", user.tg_id, teacher.teacher_id, result["lessons"])
        return _json(result)

    routes = [("GET", "/lessons", lessons), ("GET", "/lessons/{lid}", lesson), ("DELETE", "/lessons/{lid}", lesson_delete),
              ("GET", "/record/options", record_options_view), ("POST", "/record", record_create_view)]
    for method, path, handler in routes:
        app.router.add_route(method, prefix + path, guard(handler))
    logger.info("Admin API (занятия): %d маршрутов", len(routes))
