"""HTTP API кабинета спортсмена Telegram Mini App — /api/athlete/*.

Тонкий слой над `DiaryService`: те же записи, задания, оценки и рейтинг, что в
боте (`bot/handlers/athlete/`). Отличие кабинета — запись тренировки одной формой
вместо мастера из пяти шагов; всё остальное считает сервис, поэтому очки и места
совпадают с ботом.

Спортсмен определяется по `students.athlete_tg_id`, видит только свой дневник;
оценки ставит педагог, здесь они только показываются.

Авторизация — `Authorization: tma <initData>`; для локальной разработки `dev`.
"""
from __future__ import annotations

import logging

from aiohttp import web

from bot.api.admin import auth_tg_id
from bot.services.diary_service import place_icon
from bot.utils.dates import current_period
from bot.utils.diary_topics import ALL_TOPICS

logger = logging.getLogger(__name__)

PREFIX = "/api/athlete"
MAX_MINUTES = 600


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _dp_get(dp, key: str):
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


def register_athlete_api(app: web.Application, dp) -> None:
    """Уведомлений отсюда не шлём: новые записи педагоги видят в своём кабинете."""
    diary_service = _dp_get(dp, "diary_service")
    teacher_repo = dp["teacher_repo"]

    def athlete_only(handler):
        """Спортсмен = tg_id записан в `students.athlete_tg_id`."""
        async def wrapped(request: web.Request) -> web.Response:
            if diary_service is None:
                return _json({"error": "unavailable"}, status=503)
            tg_id = auth_tg_id(request)
            if tg_id is None:
                return _json({"error": "unauthorized"}, status=401)
            student = await diary_service.athlete_by_tg(tg_id)
            if student is None:
                return _json({"error": "forbidden"}, status=403)
            try:
                return await handler(request, tg_id, student)
            except web.HTTPException:
                raise
            except Exception as exc:
                logger.exception("Athlete API %s: %s", request.path, exc)
                return _json({"error": "internal"}, status=500)
        return wrapped

    async def _body(request: web.Request) -> dict:
        try:
            return await request.json() or {}
        except Exception:
            return {}

    async def _entry_rows(student, period: str) -> list[dict]:
        entries = await diary_service.entries_for_student(student.student_id, period=period)
        tasks = await diary_service.tasks_map(student.student_id)
        teachers = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
        return [{
            "id": e.entry_id, "date": e.date, "minutes": e.minutes,
            "topics": list(e.topics or []), "comment": e.comment,
            "grade": e.grade, "gradeComment": e.grade_comment,
            "gradedBy": teachers.get(e.graded_by, ""),
            "tasks": [tasks[t].exercise for t in (e.task_ids or []) if t in tasks],
            "canDelete": not e.grade,
        } for e in sorted(entries, key=lambda e: e.date, reverse=True)]

    async def _place(student, period: str) -> tuple[int | None, str]:
        board = await diary_service.leaderboard(period)
        row = next((r for r in board if r.student_id == student.student_id), None)
        return (row.place if row else None), (place_icon(row.place) if row else "")

    def _stats(st) -> dict:
        return {"sessions": st.sessions, "minutes": st.total_minutes,
                "points": st.points, "avgGrade": st.avg_grade, "byTopic": st.by_topic}

    async def _open_tasks(student) -> list[dict]:
        usage = await diary_service.task_usage(student.student_id)
        out = []
        for t in await diary_service.open_tasks(student.student_id):
            done, last = usage.get(t.task_id, (0, ""))
            out.append({"id": t.task_id, "exercise": t.exercise, "minutes": t.minutes,
                        "comment": t.comment, "done": done, "last": last})
        return out

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def me(request: web.Request, tg_id, student) -> web.Response:
        return _json({
            "tgId": tg_id, "period": current_period(), "name": student.name,   # имя в приветствии
            "student": {"id": student.student_id, "name": student.name},
            "topics": await diary_service.topics_for(student),
            "allTopics": ALL_TOPICS,
        })

    async def home(request: web.Request, tg_id, student) -> web.Response:
        period = request.query.get("ym") or current_period()
        st = await diary_service.stats(student.student_id, period)
        place, icon = await _place(student, period)
        rows = await _entry_rows(student, period)
        return _json({
            "period": period, "student": {"id": student.student_id, "name": student.name},
            "stats": _stats(st), "place": place, "placeIcon": icon,
            "recent": rows[:3], "unrated": sum(1 for r in rows if not r["grade"]),
            "openTasks": await _open_tasks(student),
        })

    # ── записи тренировок ────────────────────────────────────────────────
    async def entries(request: web.Request, tg_id, student) -> web.Response:
        period = request.query.get("ym") or current_period()
        st = await diary_service.stats(student.student_id, period)
        return _json({"period": period, "stats": _stats(st),
                      "entries": await _entry_rows(student, period)})

    async def entry_create(request: web.Request, tg_id, student) -> web.Response:
        body = await _body(request)
        date_str = str(body.get("date") or "")[:10]
        try:
            minutes = int(body.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if len(date_str) != 10 or not 1 <= minutes <= MAX_MINUTES:
            return _json({"error": "bad_request", "message": "Нужны дата и минуты"}, status=400)
        allowed = set(await diary_service.topics_for(student))
        topics = [t for t in (body.get("topics") or []) if t in allowed]
        open_ids = {t.task_id for t in await diary_service.open_tasks(student.student_id)}
        task_ids = [t for t in (body.get("taskIds") or []) if t in open_ids]
        entry = await diary_service.create_entry(
            student.student_id, date_str, minutes, topics, task_ids,
            str(body.get("comment") or "").strip()[:500],
        )
        logger.info("Кабинет спортсмена: запись %s — %s %s мин", entry.entry_id, student.student_id, minutes)
        return _json({"ok": True, "id": entry.entry_id})

    async def entry_delete(request: web.Request, tg_id, student) -> web.Response:
        eid = request.match_info["eid"]
        if not await diary_service.delete_entry(eid, student.student_id):
            return _json({"error": "not_found", "message": "Запись с оценкой удалить нельзя"}, status=409)
        return _json({"ok": True})

    # ── задания и рейтинг ────────────────────────────────────────────────
    async def tasks(request: web.Request, tg_id, student) -> web.Response:
        return _json({"tasks": await _open_tasks(student)})

    async def rating(request: web.Request, tg_id, student) -> web.Response:
        period = request.query.get("ym") or current_period()
        topic = request.query.get("topic") or None
        if topic and topic not in ALL_TOPICS:
            topic = None
        board = await diary_service.leaderboard(period, topic)
        rows = [{"id": r.student_id, "name": r.name, "points": r.points, "minutes": r.minutes,
                 "sessions": r.sessions, "avgGrade": r.avg_grade, "place": r.place,
                 "placeIcon": place_icon(r.place), "me": r.student_id == student.student_id}
                for r in board]
        mine = next((r for r in rows if r["me"]), None)
        return _json({"period": period, "topic": topic or "", "topics": ALL_TOPICS,
                      "top": rows[:10], "me": mine, "total": len(rows)})

    routes = [
        ("GET", "/me", me), ("GET", "/home", home),
        ("GET", "/entries", entries), ("POST", "/entries", entry_create),
        ("DELETE", "/entries/{eid}", entry_delete),
        ("GET", "/tasks", tasks), ("GET", "/rating", rating),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, athlete_only(handler))
    logger.info("Athlete API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
