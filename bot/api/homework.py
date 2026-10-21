"""Домашние задания в кабинете педагога (решение владельца 15.10.2026).

Педагог выдаёт задание всей своей группе, нескольким ученикам или одному (из карточки ученика): в листе `athlete_tasks` — строка на
каждого с общим номером выдачи `batch_id` (HW-…). Кабинет спортсмена не нужен: задание уходит сообщением
родителям (и самому ученику, если кабинет есть), видно в «Дневнике» кабинета родителя.
За выполнение педагог ставит каждому оценку по своей шкале (`settings.homework_scale`: 1–5, у Яковлевой 1–10)
с комментарием — она закрывает задание и тоже уходит родителям. На рейтинг спортсменов (очки за тренировки)
оценка задания не влияет.

У заданий группы номера по порядку выдачи — «Задание №3 от 15.10» (`DiaryService.homework_numbers`): так из
сообщения об оценке понятно, за какое из нескольких заданий она поставлена.
"""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiohttp import web

from bot.services.rosters import group_members
from bot.utils.dates import format_date_display
from config.settings import settings

logger = logging.getLogger(__name__)

EXERCISE_MAX = 300
_background: set = set()          # ссылки на фоновые рассылки — иначе задачу может собрать GC


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _minutes(m: int) -> str:
    return f" — {m} мин" if m else ""


def homework_title(number: int, created_at: str) -> str:
    """«Задание №3 от 15.10» — как задание называют в сообщениях и экранах."""
    return f"Задание №{number} от {format_date_display(created_at[:10])[:5]}" if number else "Задание"


def short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def homework_routes(dp, diary_service, notifier, own_group_ids, group_names) -> list[tuple]:
    """Маршруты `/api/teacher/homework*` (обёртку авторизации педагога ставит teacher.py)."""
    student_repo = dp["student_repo"]
    student_group_repo = dp["student_group_repo"]

    def _unavailable():
        return _json({"error": "unavailable"}, status=503)

    async def _send(addrs_texts: list) -> None:
        if notifier is None:
            return
        for addrs, text in addrs_texts:
            try:
                await notifier.send_many(addrs, text)
            except Exception as exc:                    # рассылка не должна ронять выдачу
                logger.warning("Домашнее задание: сообщение не ушло: %s", exc)

    def _send_later(addrs_texts: list) -> None:
        task = asyncio.create_task(_send(addrs_texts))
        _background.add(task)
        task.add_done_callback(_background.discard)

    def _batch_row(tasks: list, names: dict, numbers: dict, students: dict) -> dict:
        t0 = tasks[0]
        return {"id": t0.batch_id, "number": numbers.get(t0.batch_id, 0), "exercise": t0.exercise,
                "minutes": t0.minutes, "comment": t0.comment, "groupId": t0.group_id,
                "groupName": names.get(t0.group_id, ""), "createdAt": t0.created_at,
                "total": len(tasks), "graded": sum(1 for t in tasks if t.grade),
                # индивидуальное задание (один ученик) — в списке видно, кому
                "single": students.get(t0.student_id, t0.student_id) if len(tasks) == 1 else ""}

    async def hw_list(request: web.Request, user, teacher) -> web.Response:
        """Выдачи педагога (новые первыми), `?gid=` — одной группы; плюс свои группы для «выдать»."""
        if diary_service is None:
            return _unavailable()
        gid = request.query.get("gid") or ""
        names, numbers = await group_names(), await diary_service.homework_numbers()
        students = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        own = await own_group_ids(teacher)
        batches: dict[str, list] = {}
        for t in await diary_service.teacher_homework(teacher.teacher_id):
            if not gid or t.group_id == gid:
                batches.setdefault(t.batch_id, []).append(t)
        return _json({
            "groups": sorted(({"id": g, "name": names.get(g, g)} for g in own), key=lambda g: g["name"]),
            "groupId": gid, "groupName": names.get(gid, "") if gid else "",
            "scale": settings.homework_scale(teacher.teacher_id),
            "batches": [_batch_row(ts, names, numbers, students) for ts in batches.values()],
        })

    async def hw_batch(request: web.Request, user, teacher) -> web.Response:
        if diary_service is None:
            return _unavailable()
        bid = request.match_info["bid"]
        tasks = [t for t in await diary_service.teacher_homework(teacher.teacher_id) if t.batch_id == bid]
        if not tasks:
            return _json({"error": "not_found"}, status=404)
        students = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        return _json({
            **_batch_row(tasks, await group_names(), await diary_service.homework_numbers(), students),
            "scale": settings.homework_scale(teacher.teacher_id),
            "students": sorted(({"taskId": t.task_id, "studentId": t.student_id,
                                 "name": students.get(t.student_id, t.student_id), "grade": t.grade,
                                 "gradeComment": t.grade_comment, "status": t.status} for t in tasks),
                               key=lambda r: r["name"]),
        })

    async def hw_create(request: web.Request, user, teacher) -> web.Response:
        """Выдать задание: {groupId, studentIds, exercise, minutes?, comment?} — всей группе или выбранным."""
        if diary_service is None:
            return _unavailable()
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        gid = (body or {}).get("groupId") or ""
        exercise = ((body or {}).get("exercise") or "").strip()[:EXERCISE_MAX]
        comment = ((body or {}).get("comment") or "").strip()[:EXERCISE_MAX]
        minutes = (body or {}).get("minutes") or 0
        wanted = [x for x in ((body or {}).get("studentIds") or []) if isinstance(x, str)]
        if gid not in await own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        if not exercise:
            return _json({"error": "bad_request", "message": "Напишите, что задано"}, status=400)
        if not isinstance(minutes, int) or not 0 <= minutes <= 600:
            return _json({"error": "bad_request", "message": "Минуты — число от 0 до 600"}, status=400)
        members = {s.student_id: s for s in await group_members(student_repo, student_group_repo, gid)}
        ids = [sid for sid in dict.fromkeys(wanted) if sid in members]
        if not ids:
            return _json({"error": "bad_request", "message": "Отметьте хотя бы одного ученика"}, status=400)
        tasks = await diary_service.create_homework(teacher.teacher_id, gid, ids, exercise, minutes, comment)
        number = (await diary_service.homework_numbers()).get(tasks[0].batch_id, 0)
        title = homework_title(number, tasks[0].created_at)
        group = (await group_names()).get(gid, "")
        note = f"\n💬 {escape(comment)}" if comment else ""
        what = f"<b>{escape(exercise)}</b>{_minutes(minutes)}{note}"
        msgs = []
        for t in tasks:
            s = members[t.student_id]
            msgs.append((s.athlete_addrs, f"📋 <b>Домашнее задание</b>\n{title} · {escape(teacher.name)}\n\n{what}"))
            msgs.append((s.parent_addrs, f"📋 <b>{escape(s.name)}</b>: новое домашнее задание\n"
                                         f"{title} · {escape(teacher.name)}, {escape(group)}\n\n{what}\n\n"
                                         f"Оценку за выполнение педагог поставит в дневнике."))
        _send_later(msgs)
        logger.info("Mini App: педагог %s выдал задание %s (№%d) группе %s — %d", teacher.teacher_id,
                    tasks[0].batch_id, number, gid, len(tasks))
        return _json({"ok": True, "id": tasks[0].batch_id, "number": number, "count": len(tasks)})

    async def hw_grade(request: web.Request, user, teacher) -> web.Response:
        """Оценка за выполнение по шкале педагога: {grade, comment}; можно поменять. Сообщение ученику и родителям."""
        if diary_service is None:
            return _unavailable()
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        grade, comment = (body or {}).get("grade"), ((body or {}).get("comment") or "").strip()
        task = await diary_service.task(request.match_info["tid"])
        if task is None or task.teacher_id != teacher.teacher_id:
            return _json({"error": "not_found"}, status=404)
        scale = settings.homework_scale(teacher.teacher_id)
        if not isinstance(grade, int) or not 1 <= grade <= scale:
            return _json({"error": "bad_request", "message": f"Оценка — число от 1 до {scale}"}, status=400)
        updated = await diary_service.grade_task(task.task_id, grade, comment, scale=scale)
        if updated is None:
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(task.student_id)
        if s is not None:
            title = homework_title((await diary_service.homework_numbers()).get(task.batch_id, 0), task.created_at)
            note = f"\n📝 {escape(updated.grade_comment)}" if updated.grade_comment else ""
            what = f"{title} «{escape(short(task.exercise))}»: <b>{grade}/{scale}</b>{note}"
            _send_later([
                (s.athlete_addrs, f"⭐ <b>{escape(teacher.name)}</b> оценил(а) домашнее задание\n{what}"),
                (s.parent_addrs, f"⭐ <b>{escape(s.name)}</b>: оценка за домашнее задание "
                                 f"({escape(teacher.name)})\n{what}"),
            ])
        logger.info("Mini App: педагог %s оценил задание %s на %s/%s", teacher.teacher_id, task.task_id, grade, scale)
        return _json({"ok": True, "grade": updated.grade, "gradeComment": updated.grade_comment, "scale": scale})

    return [
        ("GET", "/homework", hw_list), ("POST", "/homework", hw_create),
        ("GET", "/homework/{bid}", hw_batch), ("POST", "/homework/tasks/{tid}/grade", hw_grade),
    ]
