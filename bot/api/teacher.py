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

from bot.api.admin import auth_tg_id
from bot.api.record import RecordError, record_create, record_options
from bot.models import TeacherPeriodSubmission
from bot.models.enums import LessonType
from bot.services import LessonService
from bot.services.billing_service import calc_earned
from bot.services.rosters import group_members
from bot.utils.attendees import free_attendee_label, has_amount_snapshots, parse_attendees
from bot.utils.dates import current_period, last_periods, now_str
from bot.utils.groups import hide_service_groups
from bot.utils.ids import generate_submission_id
from bot.utils.locks import InProgressGuard
from config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "/api/teacher"
_submitting = InProgressGuard()


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


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


def register_teacher_api(app: web.Application, dp) -> None:
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

    routes = [
        ("GET", "/me", me), ("GET", "/home", home),
        ("GET", "/lessons", lessons), ("GET", "/lessons/{lid}", lesson), ("DELETE", "/lessons/{lid}", lesson_delete),
        ("GET", "/record/options", record_options_view), ("POST", "/record", record_create_view),
        ("GET", "/groups", groups), ("GET", "/groups/{gid}", group), ("GET", "/students/{sid}", student),
        ("GET", "/stats", stats), ("GET", "/submit", submit_preview), ("POST", "/submit", submit),
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, teacher_only(handler))
    logger.info("Teacher API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
