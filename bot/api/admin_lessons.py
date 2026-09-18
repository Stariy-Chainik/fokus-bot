"""Кабинет администратора: занятия за день, карточка занятия, удаление (замок периода обходится, как в боте)."""
from __future__ import annotations

import logging
from datetime import date

from aiohttp import web

from bot.models.enums import LessonType
from bot.services.billing_service import calc_earned
from bot.api.record import RecordError, record_create, record_options
from bot.utils.attendees import parse_attendees

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

    async def _locked(lesson) -> bool:
        return await submission_repo.get_by_teacher_and_period(lesson.teacher_id, lesson.date[:7]) is not None

    def _slot_names(lesson) -> list[str]:
        return [n for n in (lesson.student_1_name, lesson.student_2_name,
                            getattr(lesson, "student_3_name", None), getattr(lesson, "student_4_name", None)) if n]

    async def lessons(request: web.Request, user) -> web.Response:
        day = request.query.get("date") or date.today().isoformat()
        if len(day) != 10:
            return _json({"error": "bad_request"}, status=400)
        teachers = {t.teacher_id: t for t in await teacher_repo.get_all()}
        groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        students = {s.student_id: s.name for s in await student_repo.get_all()}
        out = []
        for ls in sorted((x for x in await lesson_repo.get_all() if x.date == day), key=lambda x: x.recorded_at or ""):
            t = teachers.get(ls.teacher_id)
            names = (_slot_names(ls) if ls.type != LessonType.GROUP
                     else [students.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees)])
            out.append({
                "id": ls.lesson_id, "date": ls.date, "teacherId": ls.teacher_id,
                "teacherName": t.name if t else ls.teacher_name, "type": ls.type.value,
                "groupName": groups[ls.group_id].name if ls.group_id in groups else "",
                "students": names, "durationMin": ls.duration_min,
                "earned": calc_earned(ls.type, ls.duration_min, t, ls.group_id, ls.attendees, ls.date[:7]) if t else 0,
                "recordedAt": ls.recorded_at, "locked": await _locked(ls),
            })
        return _json({"date": day, "lessons": out, "earned": sum(x["earned"] for x in out)})

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
            "attendees": attendees, "recordedAt": ls.recorded_at,
            "earned": calc_earned(ls.type, ls.duration_min, t, ls.group_id, ls.attendees, ls.date[:7]) if t else 0,
            "locked": await _locked(ls),
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
