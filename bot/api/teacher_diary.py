"""Дневники спортсменов в кабинете педагога — `/api/teacher/diary*`.

Выделено из `bot/api/teacher.py`: оценка записей тренировок, задания спортсменам,
рейтинг и защита от двойного создания задания (`_task_locks`).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from bot.services.diary_service import place_icon
from bot.services.parent_notifier import notify_athlete
from bot.utils.dates import current_period, format_date_display

logger = logging.getLogger(__name__)


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def diary_routes(diary_service: Any, notifier: Any, bot: Any = None) -> list[tuple[str, str, Any]]:
    """Маршруты `/api/teacher/diary*` (обёртку `teacher_only` ставит `teacher.py`)."""
    _task_locks: dict[str, asyncio.Lock] = {}

    def _entry(e, tasks: dict) -> dict:
        return {
            "id": e.entry_id, "date": e.date, "minutes": e.minutes, "topics": list(e.topics or []),
            "comment": e.comment, "grade": e.grade, "gradeComment": e.grade_comment,
            "tasks": [tasks[t].exercise for t in (e.task_ids or []) if t in tasks],
        }

    async def _athletes(teacher) -> list:
        return await diary_service.athletes_for_teacher(teacher.teacher_id, is_admin=False)

    def diary_only(handler):
        async def wrapped(request: web.Request, user, teacher) -> web.Response:
            if diary_service is None:
                return _json({"error": "unavailable"}, status=503)
            return await handler(request, user, teacher)
        return wrapped

    def _task_lock(sid: str) -> asyncio.Lock:
        """Два одинаковых запроса в одну секунду — второй ждёт первого и видит его задание."""
        if len(_task_locks) > 512:
            for k in [k for k, lk in _task_locks.items() if not lk.locked() and k != sid]:
                _task_locks.pop(k, None)
        return _task_locks.setdefault(sid, asyncio.Lock())

    @diary_only
    async def diary(request: web.Request, user, teacher) -> web.Response:
        athletes = await _athletes(teacher)
        unrated = await diary_service.unrated_counts([a.student_id for a in athletes])
        period = request.query.get("ym") or current_period()
        return _json({
            "period": period,
            "athletes": [{"id": a.student_id, "name": a.name, "unrated": unrated.get(a.student_id, 0)}
                         for a in sorted(athletes, key=lambda x: x.name)],
        })

    @diary_only
    async def diary_student(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        athletes = {a.student_id: a for a in await _athletes(teacher)}
        s = athletes.get(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        entries = await diary_service.entries_for_student(sid, period=period)
        tasks = await diary_service.tasks_map(sid)
        st = await diary_service.stats(sid, period)
        board = await diary_service.leaderboard(period)
        row = next((r for r in board if r.student_id == sid), None)
        return _json({
            "student": {"id": sid, "name": s.name}, "period": period,
            "stats": {"sessions": st.sessions, "minutes": st.total_minutes, "points": st.points,
                      "graded": st.graded, "avgGrade": st.avg_grade, "byTopic": st.by_topic},
            "place": row.place if row else None, "placeIcon": place_icon(row.place) if row else "",
            "entries": [_entry(e, tasks) for e in sorted(entries, key=lambda e: e.date, reverse=True)],
            "openTasks": [{"id": t.task_id, "exercise": t.exercise, "minutes": t.minutes, "comment": t.comment,
                           "videos": len(t.videos), "mine": t.teacher_id == teacher.teacher_id}
                          for t in await diary_service.open_tasks(sid)],
        })

    @diary_only
    async def diary_grade(request: web.Request, user, teacher) -> web.Response:
        eid = request.match_info["eid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        grade, comment = (body or {}).get("grade"), ((body or {}).get("comment") or "").strip()
        entry = await diary_service.entry(eid)
        if entry is None:
            return _json({"error": "not_found"}, status=404)
        athletes = {a.student_id: a for a in await _athletes(teacher)}
        s = athletes.get(entry.student_id)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        if not isinstance(grade, int):
            return _json({"error": "bad_request", "message": "Оценка — число от 1 до 5"}, status=400)
        try:
            updated = await diary_service.grade_entry(eid, grade, comment, teacher.teacher_id)
        except ValueError:
            return _json({"error": "bad_request", "message": "Оценка — число от 1 до 5"}, status=400)
        if updated is None:
            return _json({"error": "not_found"}, status=404)
        topics = ", ".join(entry.topics) if entry.topics else "—"
        note = f"\n📝 {comment}" if comment else ""
        if bot is not None or notifier is not None:
            await notify_athlete(s, f"⭐ <b>{teacher.name}</b> оценил(а) вашу тренировку {format_date_display(entry.date)} "
                                    f"({entry.minutes} мин, {topics}): <b>{grade}/5</b>{note}", bot, notifier)
        if notifier is not None:
            await notifier.send_many(s.parent_addrs,
                                     f"⭐ <b>{s.name}</b>: тренировка {format_date_display(entry.date)} "
                                     f"({entry.minutes} мин, {topics}) оценена педагогом {teacher.name}: "
                                     f"<b>{grade}/5</b>{note}")
        logger.info("Mini App: педагог %s оценил запись %s на %s", teacher.teacher_id, eid, grade)
        return _json({"ok": True, "grade": updated.grade, "gradeComment": updated.grade_comment})

    @diary_only
    async def diary_tasks(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        athletes = {a.student_id: a for a in await _athletes(teacher)}
        if sid not in athletes:
            return _json({"error": "not_found"}, status=404)
        usage = await diary_service.task_usage(sid)
        rows = (await diary_service.tasks_map(sid)).values()
        return _json({
            "student": {"id": sid, "name": athletes[sid].name},
            "tasks": [{"id": t.task_id, "exercise": t.exercise, "minutes": t.minutes, "comment": t.comment,
                       "status": t.status, "doneTimes": usage.get(t.task_id, (0, ""))[0],
                       "lastDone": usage.get(t.task_id, (0, ""))[1],
                       # видео выполнения (02.11.2026): переслать в Telegram можно только своё задание
                       "videos": len(t.videos), "mine": t.teacher_id == teacher.teacher_id}
                      for t in sorted(rows, key=lambda t: (t.status != "open", t.created_at), reverse=False)],
            "recent": await diary_service.recent_exercises(teacher.teacher_id),
        })

    @diary_only
    async def diary_task_add(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        exercise = ((body or {}).get("exercise") or "").strip()
        minutes, comment = (body or {}).get("minutes"), ((body or {}).get("comment") or "").strip()
        athletes = {a.student_id: a for a in await _athletes(teacher)}
        if sid not in athletes:
            return _json({"error": "not_found"}, status=404)
        if not exercise or not isinstance(minutes, int) or minutes <= 0:
            return _json({"error": "bad_request", "message": "Нужно упражнение и минуты"}, status=400)
        async with _task_lock(sid):
            # двойное нажатие «Выдать задание» (02.11.2026): то же задание за минуту — отдаём первое, второго нет
            dup = await diary_service.recent_task_duplicate(sid, teacher.teacher_id, exercise, minutes, comment)
            if dup is not None:
                logger.info("Mini App: педагог %s повторно выдал задание %s ученику %s — повтор", teacher.teacher_id, dup.task_id, sid)
                return _json({"ok": True, "id": dup.task_id, "repeat": True})
            task = await diary_service.create_task(sid, teacher.teacher_id, exercise, minutes, comment)
        note = f"\n💬 {comment}" if comment else ""
        if bot is not None or notifier is not None:
            await notify_athlete(athletes[sid], f"📋 <b>Новое задание от {teacher.name}</b>\n\n<b>{task.exercise}</b> — "
                                 f"{task.minutes} мин{note}\n\nОтмечайте задание при записи каждой тренировки.", bot, notifier)
        logger.info("Mini App: педагог %s выдал задание %s ученику %s", teacher.teacher_id, task.task_id, sid)
        return _json({"ok": True, "id": task.task_id})

    @diary_only
    async def diary_task_close(request: web.Request, user, teacher) -> web.Response:
        task = await diary_service.task(request.match_info["tid"])
        athletes = {a.student_id for a in await _athletes(teacher)}
        if task is None or task.student_id not in athletes:
            return _json({"error": "not_found"}, status=404)
        closed = await diary_service.close_task(task.task_id)
        return _json({"ok": closed is not None})

    @diary_only
    async def diary_rating(request: web.Request, user, teacher) -> web.Response:
        period = request.query.get("ym") or current_period()
        topic = request.query.get("topic") or None
        board = await diary_service.leaderboard(period, topic)
        mine = {a.student_id for a in await _athletes(teacher)}
        return _json({
            "period": period, "topic": topic or "",
            "rows": [{"id": r.student_id, "name": r.name, "points": r.points, "minutes": r.minutes,
                      "sessions": r.sessions, "avgGrade": r.avg_grade, "place": r.place,
                      "icon": place_icon(r.place), "mine": r.student_id in mine} for r in board],
        })

    return [
        ("GET", "/diary", diary),
        ("GET", "/diary/rating", diary_rating),
        ("GET", "/diary/{sid}", diary_student),
        ("POST", "/diary/entries/{eid}/grade", diary_grade),
        ("GET", "/diary/{sid}/tasks", diary_tasks),
        ("POST", "/diary/{sid}/tasks", diary_task_add),
        ("POST", "/diary/tasks/{tid}/close", diary_task_close),
    ]
