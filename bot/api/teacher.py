"""HTTP API кабинета педагога Telegram Mini App — /api/teacher/*.

Тот же принцип, что и в кабинете администратора (`bot/api/admin.py`): тонкий слой
над сервисами бота, суммы считает `SalaryService`, запись занятий — общий с админом
модуль `bot/api/record.py`. Отличия роли: педагог видит только свои занятия, группы
и учеников (`TeacherVisibilityService`), замок сданного периода для него действует
(`bypass_period_lock=False`), сдать период можно с 25-го числа.

Авторизация — `Authorization: tma <initData>` + непустой `users.teacher_id`.
"""
from __future__ import annotations

import logging
from datetime import date

from aiohttp import web

from types import SimpleNamespace
from typing import Any, cast

from bot.api.admin import auth_tg_id
from bot.api.record import RecordError, record_create, record_options
from bot.handlers.admin.bills.helpers import _send_bill_to_parents, _student_group_names
from bot.models import TeacherPeriodSubmission
from bot.models.enums import LessonType
from bot.services import LessonService, payment_ledger
from bot.services.billing_service import calc_earned
from bot.services.diary_service import place_icon
from bot.services.payment_methods import ADMIN_MANUAL, CASH, RECEIPT_BANK
from bot.services.rosters import group_members
from bot.utils.dates import format_date_display
from bot.utils.notify import notify
from bot.utils.attendees import free_attendee_label, has_amount_snapshots, parse_attendees
from bot.utils.dates import current_period, last_periods, now_str
from bot.utils.groups import hide_service_groups
from bot.utils.ids import generate_submission_id
from bot.utils.locks import InProgressGuard
from config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "/api/teacher"
_submitting = InProgressGuard()
_paying = InProgressGuard()


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _dp_get(dp, key: str):
    """Необязательная зависимость (дневник, notifier): в тестах их может не быть."""
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


def _lesson_brief(ls, teacher, group_names: dict, student_names: dict) -> dict:
    """Строка списка занятий: кто занимался и сколько начислено педагогу."""
    if ls.type == LessonType.GROUP:
        who = [student_names.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
    else:
        who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
    return {
        "id": ls.lesson_id, "date": ls.date, "type": ls.type.value, "durationMin": ls.duration_min,
        "groupId": ls.group_id or "", "groupName": group_names.get(ls.group_id, ""),
        "students": who, "recordedAt": ls.recorded_at,
        "earned": calc_earned(ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date[:7]),
    }


def register_teacher_api(app: web.Application, dp, bot=None) -> None:
    user_repo = dp["user_repo"]
    teacher_repo = dp["teacher_repo"]
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    branch_repo = dp["branch_repo"]
    lesson_repo = dp["lesson_repo"]
    student_group_repo = dp["student_group_repo"]
    teacher_group_repo = dp["teacher_group_repo"]
    submission_repo = dp["submission_repo"]
    lesson_service = dp["lesson_service"]
    salary_service = dp["salary_service"]
    visibility = dp["visibility"]
    payment_service = dp["payment_service"]
    payment_repo = dp["payment_repo"]
    client_repo = dp["client_repo"]
    diary_service = _dp_get(dp, "diary_service")
    notifier = _dp_get(dp, "notifier")

    def teacher_only(handler):
        async def wrapped(request: web.Request) -> web.Response:
            tg_id = auth_tg_id(request)
            if tg_id is None:
                return _json({"error": "unauthorized"}, status=401)
            user = await user_repo.get_by_tg_id(tg_id)
            if user is None or not user.teacher_id:
                return _json({"error": "forbidden"}, status=403)
            teacher = await teacher_repo.get_by_id(user.teacher_id)
            if teacher is None:
                return _json({"error": "forbidden"}, status=403)
            try:
                return await handler(request, user, teacher)
            except web.HTTPException:
                raise
            except Exception as exc:
                logger.exception("Teacher API %s: %s", request.path, exc)
                return _json({"error": "internal"}, status=500)
        return wrapped

    async def _group_names() -> dict:
        return {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}

    async def _submitted(teacher_id: str) -> set[str]:
        return {s.period_month for s in await submission_repo.get_by_teacher(teacher_id)}

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def me(request: web.Request, user, teacher) -> web.Response:
        return _json({
            "tgId": user.tg_id, "isAdmin": user.is_admin, "teacherId": teacher.teacher_id,
            "name": teacher.name, "canBill": teacher.teacher_id in settings.billing_teacher_id_set,
        })

    async def home(request: web.Request, user, teacher) -> web.Response:
        today = date.today().isoformat()
        period = current_period()
        month = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)
        today_lessons = [ls for ls in month if ls.date == today]
        submitted = await _submitted(teacher.teacher_id)
        prev = last_periods(2)[1]
        return _json({
            "name": teacher.name, "today": today, "period": period, "prevPeriod": prev,
            "lessonsToday": len(today_lessons), "lessonsMonth": len(month),
            "earnedMonth": await salary_service.total_for(teacher, period),
            "earnedToday": await salary_service.total_for(teacher, today),
            "periodSubmitted": period in submitted, "prevSubmitted": prev in submitted,
            "canSubmit": LessonService.can_submit_period(date.today(), period),
            "groups": len(hide_service_groups(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id))),
        })

    # ── занятия ──────────────────────────────────────────────────────────
    async def lessons(request: web.Request, user, teacher) -> web.Response:
        """?date=YYYY-MM-DD — день, ?ym=YYYY-MM — месяц (по умолчанию сегодня)."""
        day, ym = request.query.get("date"), request.query.get("ym")
        key = day or ym or date.today().isoformat()
        if len(key) not in (7, 10):
            return _json({"error": "bad_request"}, status=400)
        rows = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, key)
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        groups = await _group_names()
        submitted = await _submitted(teacher.teacher_id)
        out = [_lesson_brief(ls, teacher, groups, names)
               for ls in sorted(rows, key=lambda x: (x.date, x.recorded_at or ""))]
        for item in out:
            item["locked"] = item["date"][:7] in submitted
        return _json({
            "key": key, "isDay": len(key) == 10, "lessons": out,
            "earned": await salary_service.total_for(teacher, key),
        })

    async def lesson(request: web.Request, user, teacher) -> web.Response:
        ls = await lesson_repo.get_by_id(request.match_info["lid"])
        if ls is None or ls.teacher_id != teacher.teacher_id:
            return _json({"error": "not_found"}, status=404)
        g = await group_repo.get_by_id(ls.group_id) if ls.group_id else None
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        if ls.type == LessonType.GROUP:
            attendees = [{"studentId": e.student_id, "name": names.get(e.student_id, e.student_id),
                          "durationMin": e.duration_min, "amount": e.amount}
                         for e in parse_attendees(ls.attendees or "", default_duration=ls.duration_min)]
        else:
            ids = [i for i in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id) if i]
            slots = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
            attendees = [{"studentId": i, "name": names.get(i, n), "durationMin": ls.duration_min, "amount": None}
                         for i, n in zip(ids, slots, strict=False)]
        return _json({
            "id": ls.lesson_id, "date": ls.date, "type": ls.type.value, "durationMin": ls.duration_min,
            "groupId": ls.group_id or "", "groupName": g.name if g else "",
            "groupMode": g.billing_mode.value if g else "",
            "freeLabel": free_attendee_label(g, has_amount_snapshots(ls.attendees)),
            "attendees": attendees, "recordedAt": ls.recorded_at,
            "earned": calc_earned(ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date[:7]),
            "locked": ls.date[:7] in await _submitted(teacher.teacher_id),
        })

    async def lesson_delete(request: web.Request, user, teacher) -> web.Response:
        lid = request.match_info["lid"]
        ls = await lesson_repo.get_by_id(lid)
        if ls is None or ls.teacher_id != teacher.teacher_id:
            return _json({"error": "not_found"}, status=404)
        try:
            ok = await lesson_service.delete(lid, bypass_period_lock=False)
        except PermissionError as exc:
            return _json({"error": "period_locked", "message": str(exc)}, status=409)
        if ok:
            logger.info("Mini App: педагог %s удалил занятие %s", teacher.teacher_id, lid)
        return _json({"ok": ok}, status=200 if ok else 404)

    # ── запись занятия (замок периода действует) ─────────────────────────
    async def record_options_view(request: web.Request, user, teacher) -> web.Response:
        return _json(await record_options(dp, teacher.teacher_id))

    async def record_create_view(request: web.Request, user, teacher) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return _json({"error": "bad_request"}, status=400)
        try:
            result = await record_create(dp, teacher, body, bypass_period_lock=False)
        except RecordError as exc:
            return _json({"error": exc.code, "message": str(exc)}, status=exc.status)
        logger.info("Mini App: педагог %s отметил занятие: %s", teacher.teacher_id, result["lessons"])
        return _json(result)

    # ── мои группы и ученики ─────────────────────────────────────────────
    async def groups(request: web.Request, user, teacher) -> web.Response:
        gids = set(hide_service_groups(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id)))
        branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
        counts = {gid: len(await student_group_repo.get_students_for_group(gid)) for gid in gids}
        out = [{
            "id": g.group_id, "name": g.name, "branchName": branches.get(g.branch_id, ""),
            "mode": g.billing_mode.value, "priceFull": g.price_full, "students": counts.get(g.group_id, 0),
        } for g in sorted(await group_repo.get_all(), key=lambda g: (branches.get(g.branch_id, ""), g.sort_order, g.name))
            if g.group_id in gids]
        return _json({"groups": out})

    async def group(request: web.Request, user, teacher) -> web.Response:
        gid = request.match_info["gid"]
        gids = set(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id))
        g = await group_repo.get_by_id(gid)
        if g is None or gid not in gids:
            return _json({"error": "not_found"}, status=404)
        members = await group_members(student_repo, student_group_repo, gid)
        by_id = {s.student_id: s for s in members}
        pairs, seen, solo = [], set(), []
        for s in members:
            partner = by_id.get(s.partner_id or "")
            if partner is None:
                solo.append(s)
                continue
            key = tuple(sorted([s.student_id, partner.student_id]))
            if key not in seen:
                seen.add(key)
                pairs.append({"aId": s.student_id, "aName": s.name, "bId": partner.student_id, "bName": partner.name})
        return _json({
            "id": g.group_id, "name": g.name, "mode": g.billing_mode.value,
            "priceFull": g.price_full, "priceShort": g.price_short,
            "students": [{"id": s.student_id, "name": s.name, "tier": s.group_tier.value,
                          "partnerId": s.partner_id or ""} for s in members],
            "pairs": pairs, "soloists": [{"id": s.student_id, "name": s.name} for s in solo],
        })

    async def student(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        groups_map = await _group_names()
        partner = await student_repo.get_by_id(s.partner_id) if s.partner_id else None
        ym = request.query.get("ym") or current_period()
        mine = [ls for ls in await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, ym)
                if sid in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id)
                or sid in [e.student_id for e in parse_attendees(ls.attendees or "")]]
        names = {x.student_id: x.name for x in await student_repo.get_all()}
        return _json({
            "id": s.student_id, "name": s.name, "tier": s.group_tier.value,
            "partner": {"id": partner.student_id, "name": partner.name} if partner else None,
            "groups": [groups_map.get(g, g) for g in await student_group_repo.get_groups_for_student(sid)],
            "period": ym,
            "lessons": [_lesson_brief(ls, teacher, groups_map, names)
                        for ls in sorted(mine, key=lambda x: x.date)],
        })

    # ── деньги и сдача периода ───────────────────────────────────────────
    async def stats(request: web.Request, user, teacher) -> web.Response:
        ym = request.query.get("ym") or current_period()
        lines = await salary_service.lines_for(teacher, ym)
        rows = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, ym)
        submitted = await _submitted(teacher.teacher_id)
        names = {s.student_id: s.name for s in await student_repo.get_all()}
        groups = await _group_names()
        by_id = {ls.lesson_id: ls for ls in rows}

        def label_for(ln) -> str:
            """У строк-занятий label пустой (в боте он не нужен) — подставляем, кто занимался."""
            if ln.label:
                return ln.label
            ls = by_id.get(ln.lesson_id or "")
            if ls is None:
                return ""
            if ls.type == LessonType.GROUP:
                return groups.get(ls.group_id, "Группа")
            who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
            return " + ".join(who) or names.get(ls.student_1_id or "", "Занятие")

        return _json({
            "period": ym, "total": sum(ln.amount for ln in lines),
            "groupLessons": sum(1 for ls in rows if ls.type == LessonType.GROUP),
            "individualLessons": sum(1 for ls in rows if ls.type != LessonType.GROUP),
            "submitted": ym in submitted,
            "lines": [{"date": ln.date, "kind": ln.kind, "label": label_for(ln),
                       "minutes": ln.minutes, "amount": ln.amount, "lessonId": ln.lesson_id} for ln in lines],
        })

    async def submit_preview(request: web.Request, user, teacher) -> web.Response:
        ym = request.query.get("ym") or current_period()
        lessons_, _t, total = await lesson_service.preview_period(teacher.teacher_id, ym)
        submitted = await _submitted(teacher.teacher_id)
        return _json({
            "period": ym, "lessons": len(lessons_), "total": total,
            "submitted": ym in submitted,
            "canSubmit": LessonService.can_submit_period(date.today(), ym),
            "periods": [{"ym": p, "submitted": p in submitted,
                         "canSubmit": LessonService.can_submit_period(date.today(), p)} for p in last_periods(3)],
        })

    async def submit(request: web.Request, user, teacher) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        ym = (body or {}).get("ym") or current_period()
        if not isinstance(ym, str) or len(ym) != 7:
            return _json({"error": "bad_request"}, status=400)
        if not LessonService.can_submit_period(date.today(), ym):
            return _json({"error": "too_early", "message": "Сдать период можно с 25-го числа"}, status=409)
        if await submission_repo.get_by_teacher_and_period(teacher.teacher_id, ym):
            return _json({"error": "already", "message": "Период уже сдан"}, status=409)
        key = f"{teacher.teacher_id}:{ym}"
        if key in _submitting:
            return _json({"error": "in_progress"}, status=409)
        _submitting.add(key)
        try:
            lessons_, _t, total = await lesson_service.preview_period(teacher.teacher_id, ym)
            sub = TeacherPeriodSubmission(
                submission_id=generate_submission_id(await submission_repo.get_existing_ids()),
                teacher_id=teacher.teacher_id, period_month=ym, submitted_at=now_str(),
                lessons_count=len(lessons_), total_earned=total,
            )
            await submission_repo.add(sub)
        finally:
            _submitting.discard(key)
        logger.info("Mini App: педагог %s сдал период %s (%d зан., %d ₽)",
                    teacher.teacher_id, ym, len(lessons_), total)
        return _json({"ok": True, "period": ym, "lessons": len(lessons_), "total": total})

    # ── дневники спортсменов ─────────────────────────────────────────────
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
            "openTasks": [{"id": t.task_id, "exercise": t.exercise, "minutes": t.minutes, "comment": t.comment}
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
        if bot is not None:
            await notify(bot, [s.athlete_tg_id],
                         f"⭐ <b>{teacher.name}</b> оценил(а) вашу тренировку {format_date_display(entry.date)} "
                         f"({entry.minutes} мин, {topics}): <b>{grade}/5</b>{note}")
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
                       "lastDone": usage.get(t.task_id, (0, ""))[1]}
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
        task = await diary_service.create_task(sid, teacher.teacher_id, exercise, minutes, comment)
        note = f"\n💬 {comment}" if comment else ""
        if bot is not None:
            await notify(bot, [athletes[sid].athlete_tg_id],
                         f"📋 <b>Новое задание от {teacher.name}</b>\n\n<b>{task.exercise}</b> — "
                         f"{task.minutes} мин{note}\n\nОтмечайте задание при записи каждой тренировки.")
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

    # ── счета своих групп (только BILLING_TEACHER_IDS) ───────────────────
    def billing_only(handler):
        async def wrapped(request: web.Request, user, teacher) -> web.Response:
            if teacher.teacher_id not in settings.billing_teacher_id_set:
                return _json({"error": "forbidden"}, status=403)
            return await handler(request, user, teacher)
        return wrapped

    async def _own_group_ids(teacher) -> set:
        return set(hide_service_groups(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id)))

    async def _bill_rows(sid: str, period: str) -> tuple[dict, dict, list]:
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        paid_map = payment_ledger.paid_sums(await payment_repo.get_by_student_and_period(sid, period))
        rows = []
        for key, agg in bills.items():
            paid = paid_map.get(key, 0)
            rows.append({"key": key, "name": agg.name, "subscription": agg.subscription, "total": agg.total,
                         "paid": min(paid, agg.total), "rest": max(agg.total - paid, 0),
                         "items": [{"date": m["date"], "durationMin": m["duration_min"],
                                    "amount": m["amount"], "paid": m["paid"]}
                                   for m in payment_ledger.lesson_marks(agg.items, paid)]})
        summary = {"total": sum(r["total"] for r in rows), "paid": sum(r["paid"] for r in rows),
                   "rest": sum(r["rest"] for r in rows)}
        return bills, summary, rows

    @billing_only
    async def bills_groups(request: web.Request, user, teacher) -> web.Response:
        period = request.query.get("ym") or current_period()
        gids = await _own_group_ids(teacher)
        groups_ = [g for g in await group_repo.get_all() if g.group_id in gids]
        out = [{"id": g.group_id, "name": g.name, "mode": g.billing_mode.value,
                "students": len(await student_group_repo.get_students_for_group(g.group_id))}
               for g in sorted(groups_, key=lambda g: g.name)]
        return _json({"period": period, "groups": out, "periods": last_periods(3)})

    @billing_only
    async def bills_group(request: web.Request, user, teacher) -> web.Response:
        gid = request.match_info["gid"]
        period = request.query.get("ym") or current_period()
        if gid not in await _own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        g = await group_repo.get_by_id(gid)
        rows = []
        for s in await group_members(student_repo, student_group_repo, gid):
            _b, summary, _r = await _bill_rows(s.student_id, period)
            rows.append({"id": s.student_id, "name": s.name, "hasParent": bool(s.parent_addrs), **summary})
        return _json({"group": {"id": gid, "name": g.name if g else gid}, "period": period, "students": rows})

    @billing_only
    async def bills_student(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        _bills, summary, rows = await _bill_rows(sid, period)
        return _json({"student": {"id": sid, "name": s.name, "hasParent": bool(s.parent_addrs)},
                      "period": period, "rows": rows, **summary,
                      "groups": await _student_group_names(sid, student_group_repo, group_repo)})

    @billing_only
    async def bills_marks(request: web.Request, user, teacher) -> web.Response:
        """Занятия ученика по одному начислению с ✅/⬜ — как экран отметки оплат у админа."""
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        key = request.query.get("key", "")
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        marks, ledger = await payment_service.teacher_lesson_marks(s, period, key)
        if ledger is None:
            return _json({"error": "not_found"}, status=404)
        return _json({
            "student": {"id": sid, "name": s.name}, "period": period,
            "ledger": {"key": key, "name": ledger.name, "group": ledger.group, "accrued": ledger.accrued,
                       "paid": ledger.paid, "remainder": ledger.remainder},
            "marks": [{"lessonId": m["lesson_id"], "date": m["date"], "durationMin": m["duration_min"],
                       "amount": m["amount"], "paid": m["paid"]} for m in marks],
        })

    @billing_only
    async def bills_pay(request: web.Request, user, teacher) -> web.Response:
        """Отметить оплату ученика своей группы: сумма зачитывается в остаток начисления."""
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        period = (body or {}).get("ym") or current_period()
        key, amount = (body or {}).get("key"), (body or {}).get("amount")
        method = (body or {}).get("method") or ADMIN_MANUAL
        if not isinstance(key, str) or not key or not isinstance(amount, int) or amount <= 0:
            return _json({"error": "bad_request", "message": "Нужны начисление и сумма"}, status=400)
        if method not in (ADMIN_MANUAL, CASH, RECEIPT_BANK):
            return _json({"error": "bad_request", "message": "Неизвестный способ оплаты"}, status=400)
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        guard = f"{sid}:{period}:{key}"
        if guard in _paying:
            return _json({"error": "in_progress"}, status=409)
        _paying.add(guard)
        try:
            credited, rows = await payment_service.record_payment(
                sid, s.name, period, amount, user.tg_id, [key],
                f"отметил педагог {teacher.name}", method,
            )
        finally:
            _paying.discard(guard)
        logger.info("Mini App: педагог %s отметил оплату %d ₽ — %s %s %s",
                    teacher.teacher_id, credited, sid, period, key)
        return _json({"credited": credited, "rows": rows})

    @billing_only
    async def bills_student_send(request: web.Request, user, teacher) -> web.Response:
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        sid = request.match_info["sid"]
        period = request.query.get("ym") or current_period()
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        if not bills:
            return _json({"error": "nothing_to_send"}, status=409)
        names = await _student_group_names(sid, student_group_repo, group_repo)
        recipients, sent_to, _ = await _send_bill_to_parents(
            cast(Any, SimpleNamespace(bot=bot)), s, period, bills, names, payment_service, client_repo,
        )
        logger.info("Mini App: педагог %s отправил счёт %s за %s", teacher.teacher_id, sid, period)
        return _json({"recipients": recipients, "sentTo": sent_to})

    @billing_only
    async def bills_group_send(request: web.Request, user, teacher) -> web.Response:
        if bot is None:
            return _json({"error": "bot_unavailable"}, status=503)
        gid = request.match_info["gid"]
        period = request.query.get("ym") or current_period()
        if gid not in await _own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        sent = no_parent = failed = skipped = 0
        for s in await group_members(student_repo, student_group_repo, gid):
            bills = await payment_service.compute_bills_for_student_period(s.student_id, period)
            if not bills:
                skipped += 1
                continue
            names = await _student_group_names(s.student_id, student_group_repo, group_repo)
            recipients, sent_to, _ = await _send_bill_to_parents(
                cast(Any, SimpleNamespace(bot=bot)), s, period, bills, names, payment_service, client_repo,
            )
            if recipients == 0:
                no_parent += 1
            elif sent_to == 0:
                failed += 1
            else:
                sent += 1
        logger.info("Mini App: педагог %s разослал счета группы %s за %s — %d",
                    teacher.teacher_id, gid, period, sent)
        return _json({"sent": sent, "noParent": no_parent, "failed": failed, "skipped": skipped})

    routes = [
        ("GET", "/me", me), ("GET", "/home", home),
        ("GET", "/lessons", lessons), ("GET", "/lessons/{lid}", lesson), ("DELETE", "/lessons/{lid}", lesson_delete),
        ("GET", "/record/options", record_options_view), ("POST", "/record", record_create_view),
        ("GET", "/groups", groups), ("GET", "/groups/{gid}", group), ("GET", "/students/{sid}", student),
        ("GET", "/stats", stats), ("GET", "/submit", submit_preview), ("POST", "/submit", submit),
        ("GET", "/diary", diary), ("GET", "/diary/rating", diary_rating), ("GET", "/diary/{sid}", diary_student),
        ("POST", "/diary/entries/{eid}/grade", diary_grade),
        ("GET", "/diary/{sid}/tasks", diary_tasks), ("POST", "/diary/{sid}/tasks", diary_task_add),
        ("POST", "/diary/tasks/{tid}/close", diary_task_close),
        ("GET", "/bills", bills_groups), ("GET", "/bills/group/{gid}", bills_group),
        ("POST", "/bills/group/{gid}/send", bills_group_send),
        ("GET", "/bills/student/{sid}", bills_student), ("POST", "/bills/student/{sid}/send", bills_student_send),
        ("GET", "/bills/student/{sid}/marks", bills_marks), ("POST", "/bills/student/{sid}/pay", bills_pay),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, teacher_only(handler))
    logger.info("Teacher API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
