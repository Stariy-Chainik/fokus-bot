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
import re
from datetime import date, timedelta

from aiohttp import web

from bot.utils.groups import lesson_group_title
from bot.api.admin import auth_tg_id
from bot.api.homework import homework_routes
from bot.api.teacher_bills import _teacher_receipts, billing_routes
from bot.api.teacher_diary import diary_routes
from bot.api.teacher_students import student_routes
from bot.api.record import RecordError, record_create, record_options
from bot.api.schedule_record import draft_day
from bot.models import TeacherPeriodSubmission
from bot.models.enums import GroupBillingMode, LessonType
from bot.services import LessonService
from bot.services.billing_service import build_billing_rows, calc_earned
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX
from bot.services import hall_rent, schedule_pilot
from bot.services.profit_service import lesson_rent
from bot.services.rosters import group_members
from bot.services.schedule_pilot import DraftConflict, active_draft_dates, edited_schedule, is_draft_completed
from bot.services.salary_summary import summarize, summary_dicts
from bot.utils.attendees import free_attendee_label, has_amount_snapshots, parse_attendees
from bot.utils.dates import current_period, last_periods, now_str
from bot.utils.groups import hide_service_groups
from bot.utils.ids import generate_submission_id
from bot.utils.locks import InProgressGuard
from config.settings import settings

__all__ = ["PREFIX", "_teacher_receipts", "direct_amount", "is_direct", "register_teacher_api"]

logger = logging.getLogger(__name__)

PREFIX = "/api/teacher"
_submitting = InProgressGuard()
_SCHEDULE_DRAFT_ID = re.compile(r"(?:[0-9]{1,10}|-[0-9]{1,11})")


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _dp_get(dp, key: str):
    """Необязательная зависимость (дневник, notifier): в тестах их может не быть."""
    data = getattr(dp, "workflow_data", dp)
    return data.get(key) if hasattr(data, "get") else None


def is_direct(ls, teacher) -> bool:
    """Занятие, которое родитель оплачивает педагогу напрямую (DIRECT_PAY_TEACHER_IDS)."""
    return (ls.type == LessonType.INDIVIDUAL
            and teacher.teacher_id in settings.direct_pay_teacher_id_set)


def direct_amount(ls, teacher) -> int:
    """Сколько родители платят педагогу за это занятие лично (школа его не начисляет)."""
    if not is_direct(ls, teacher):
        return 0
    return sum(row.amount for row in build_billing_rows(ls, teacher, include_direct=True))


def _direct_summary(lessons: list, teacher, student_names: dict) -> dict | None:
    """Блок «Прямая оплата» в зарплате: сколько должны родители и сколько аренды школе.

    None — педагог не из DIRECT_PAY_TEACHER_IDS: блока в кабинете нет.
    """
    if teacher.teacher_id not in settings.direct_pay_teacher_id_set:
        return None
    direct = [ls for ls in lessons if is_direct(ls, teacher)]
    by_student: dict[str, dict] = {}
    for ls in direct:
        for row in build_billing_rows(ls, teacher, include_direct=True):
            item = by_student.setdefault(
                row.student_id,
                {"id": row.student_id, "name": student_names.get(row.student_id, row.student_id),
                 "lessons": 0, "amount": 0},
            )
            item["lessons"] += 1
            item["amount"] += row.amount
    rents = hall_rent.rent_by_student(direct)
    for sid, r in rents.items():
        by_student.setdefault(sid, {"id": sid, "name": student_names.get(sid, sid), "lessons": 0, "amount": 0})
        by_student[sid]["rent"] = r.amount
    for item in by_student.values():
        item.setdefault("rent", 0)
    return {
        "lessons": len(direct),
        "total": sum(item["amount"] for item in by_student.values()),
        "rent": hall_rent.month_rent(direct),               # аренда зала школе: ставка педагога или ученика за час
        "students": sorted(by_student.values(), key=lambda x: -x["amount"]),
    }


def _lesson_brief(ls, teacher, group_names: dict, student_names: dict) -> dict:
    """Строка списка занятий: кто занимался и сколько начислено педагогу.

    У занятий с прямой оплатой школа начисляет 0 — вместо пустого нуля отдаём
    сумму, которую платит родитель, и аренду зала, которую педагог перечисляет школе.
    """
    if ls.type == LessonType.GROUP:
        who = [student_names.get(e.student_id, e.student_id) for e in parse_attendees(ls.attendees or "")]
    else:
        who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
    return {
        "id": ls.lesson_id, "date": ls.date, "type": ls.type.value, "durationMin": ls.duration_min,
        "groupId": ls.group_id or "", "groupName": lesson_group_title(ls, group_names),
        "students": who, "recordedAt": ls.recorded_at,
        "earned": calc_earned(ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date),
        "direct": is_direct(ls, teacher), "directAmount": direct_amount(ls, teacher),
        "rent": lesson_rent(ls),
    }


def register_teacher_api(app: web.Application, dp, bot=None) -> None:
    user_repo = dp["user_repo"]
    teacher_repo = dp["teacher_repo"]
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    lesson_repo = dp["lesson_repo"]
    student_group_repo = dp["student_group_repo"]
    teacher_group_repo = dp["teacher_group_repo"]
    submission_repo = dp["submission_repo"]
    lesson_service = dp["lesson_service"]
    salary_service = dp["salary_service"]
    visibility = dp["visibility"]
    payment_service = dp["payment_service"]
    diary_service = _dp_get(dp, "diary_service")
    notifier = _dp_get(dp, "notifier")
    hall_rent_repo = _dp_get(dp, "hall_rent_repo")
    schedule_pilots = _dp_get(dp, "schedule_pilots")

    async def _group_names() -> dict:
        return {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)}

    async def _own_group_ids(teacher) -> set:
        return set(hide_service_groups(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id)))

    bill_routes, bill_ctx = billing_routes(dp, _own_group_ids, _group_names, bot)

    def schedule_for(user):
        return schedule_pilots.for_user(user) if schedule_pilots else None

    async def schedule_students(user, teacher):
        if user.is_admin:
            return await student_repo.get_all()
        return await visibility.students_for_teacher(teacher.teacher_id)

    async def _competitions(student_id: str, limit: int = 10) -> list[dict]:
        """Результаты турниров ФТСАРР ученика — свежие первыми (лист competition_results)."""
        from bot.services import competition_results
        repo = _dp_get(dp, "competition_result_repo")
        if repo is None:
            return []
        names = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        return competition_results.view(await repo.get_for_student(student_id), names)[:limit]

    async def _rent(teacher, period: str, lessons: list | None = None) -> dict | None:
        """Аренда зала за месяц у педагога с прямой оплатой; у остальных None."""
        if teacher.teacher_id not in settings.direct_pay_teacher_id_set:
            return None
        if lessons is None:
            lessons = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)
        return await hall_rent.status(hall_rent_repo, teacher.teacher_id, period, lessons)

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

    async def _submitted(teacher_id: str) -> set[str]:
        if not settings.teacher_period_submit_enabled:      # сдача выключена — замков нет
            return set()
        return {s.period_month for s in await submission_repo.get_by_teacher(teacher_id)}

    # ── статус оплаты занятия (карточка, журнал, «сегодня» на сводке) ────
    async def _lesson_student_ids(ls, g, cache: dict) -> tuple[list[str], bool]:
        """Ученики занятия: посещаемость, слоты или — у абонемента без посещаемости — состав группы."""
        if ls.type == LessonType.GROUP:
            ids = [e.student_id for e in parse_attendees(ls.attendees or "")]
            if not ids and g is not None and g.billing_mode == GroupBillingMode.SUBSCRIPTION:
                roster: list[str] = []
                for gid in ls.group_ids:            # объединённое занятие без посещаемости — составы всех групп
                    key = ("roster", gid)
                    if key not in cache:
                        cache[key] = [m.student_id for m in await group_members(student_repo, student_group_repo, gid)]
                    roster += [sid for sid in cache[key] if sid not in roster]
                return roster, True
            return ids, False
        return [i for i in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id) if i], False

    async def _pay_statuses(ls, teacher, g, student_ids: list, students: dict, cache: dict) -> list:
        """Статус по каждому ученику: занятие с суммой — по накопительным оплатам месяца (как ✅/⬜
        у родителя), абонемент — по остатку начисления SUB:{gid}; прямую оплату школа не отслеживает."""
        if is_direct(ls, teacher):
            return [None] * len(student_ids)
        period = ls.date[:7]
        out: list[str | None] = []
        for sid in student_ids:
            if sid not in students:
                out.append(None)
                continue
            mkey = ("marks", sid, period)
            if mkey not in cache:
                cache[mkey] = await payment_service.student_lesson_marks(sid, period)
            mark = cache[mkey].mark(ls.lesson_id)
            if mark.amount > 0:
                out.append("paid" if mark.paid else "unpaid")
            elif g is not None and g.billing_mode == GroupBillingMode.SUBSCRIPTION:
                lkey = ("ledger", sid, period)
                if lkey not in cache:
                    cache[lkey] = await payment_service.ledger_for(students[sid], period, sync=False)
                # объединённое занятие — абонемент той группы, где ребёнок числится
                sub = next((cache[lkey][k] for k in (f"{SUBSCRIPTION_KEY_PREFIX}{gid}" for gid in ls.group_ids)
                            if k in cache[lkey]), None)
                out.append(None if sub is None or sub.accrued <= 0
                           else ("sub_paid" if sub.remainder <= 0 else "sub_unpaid"))
            else:
                out.append(None)
        return out

    async def _add_pay_counts(teacher, rows: list, items: list) -> None:
        """В строки журнала — «оплатили N из M» (paidCount / payableCount), одним кешем на запрос."""
        groups_by_id = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        students = {s.student_id: s for s in await student_repo.get_all(include_archived=True)}
        cache: dict = {}
        for ls, item in zip(rows, items, strict=True):
            g = groups_by_id.get(ls.group_id) if ls.group_id else None
            ids, _ = await _lesson_student_ids(ls, g, cache)
            statuses = [x for x in await _pay_statuses(ls, teacher, g, ids, students, cache) if x]
            item["paidCount"] = sum(1 for x in statuses if x in ("paid", "sub_paid"))
            item["payableCount"] = len(statuses)

    # ── профиль и сводка ─────────────────────────────────────────────────
    async def me(request: web.Request, user, teacher) -> web.Response:
        return _json({
            "tgId": user.tg_id, "isAdmin": user.is_admin, "teacherId": teacher.teacher_id,
            "name": teacher.name, "canBill": teacher.teacher_id in settings.billing_teacher_id_set,
            "periodSubmit": settings.teacher_period_submit_enabled,   # кнопка «Сдать период» и замок месяца
        })

    async def schedule_drafts(request: web.Request, user, teacher) -> web.Response:
        pilot = schedule_for(user)
        if pilot is None:
            return _json({"error": "not_found"}, status=404)
        today = schedule_pilot.moscow_today()
        today_iso, yesterday_iso = active_draft_dates(today)
        all_drafts = await pilot.drafts(await schedule_students(user, teacher))
        active = sorted(
            (d for d in all_drafts if d["date"] in (today_iso, yesterday_iso) and not is_draft_completed(d)),
            key=lambda d: (0 if d["date"] == today_iso else 1, -abs(d["id"])),
        )
        completed_today = not active and any(d["date"] == today_iso and is_draft_completed(d) for d in all_drafts)
        upcoming_dates = sorted({d["date"] for d in all_drafts if d["date"] > today_iso and not is_draft_completed(d)})
        calendar = request.query.get("view") == "calendar"
        return _json({
            "drafts": all_drafts if calendar else active,
            "today": today_iso,
            **({"yesterday": yesterday_iso, "tomorrow": (today + timedelta(days=1)).isoformat()} if calendar else {}),
            "completedToday": completed_today,
            "upcomingDates": upcoming_dates,
            "acceptWeekday": pilot.accept_weekday,
            "acceptPlainTitle": pilot.accept_plain_title,
            "acceptWeekly": pilot.accept_weekly,
            "acceptOwnerInitials": pilot.accept_owner_initials,
        })

    async def schedule_save(request: web.Request, user, teacher) -> web.Response:
        pilot = schedule_for(user)
        if pilot is None:
            return _json({"error": "not_found"}, status=404)
        mid = request.match_info["mid"]
        if not _SCHEDULE_DRAFT_ID.fullmatch(mid):
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except ValueError:
            return _json({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return _json({"error": "bad_request"}, status=400)
        revision, base = body.get("revision"), body.get("sourceRevision")
        if type(revision) is not int or revision < 0 or not isinstance(base, str) or not re.fullmatch(r"\d{1,20}:\d{1,20}", base):
            return _json({"error": "bad_request"}, status=400)
        try:
            plan = None
            if body.get("useTelegram") is not True:
                group_ids = set(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id))
                groups = {g.group_id: g.name for g in await group_repo.get_all() if g.group_id in group_ids}
                plan = edited_schedule(body, await schedule_students(user, teacher), groups)
            async with pilot.lock:
                await pilot.save_edit(int(mid), revision, base, plan)
        except ValueError as exc:
            return _json({"error": "bad_request", "message": str(exc)}, status=400)
        except KeyError:
            return _json({"error": "not_found"}, status=404)
        except DraftConflict:
            return _json({"error": "conflict", "message": "Черновик обновился. Открой последнюю версию и проверь изменения."},
                         status=409)
        return _json({"ok": True})

    async def schedule_attendance(request: web.Request, user, teacher) -> web.Response:
        mid = request.match_info["mid"]
        pilot = schedule_for(user)
        if pilot is None or not _SCHEDULE_DRAFT_ID.fullmatch(mid):
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Проверь выбранные занятия")
            results = await draft_day(dp, teacher, pilot, int(mid), body, await schedule_students(user, teacher))
        except ValueError as exc:
            return _json({"error": "bad_request", "message": str(exc)}, status=400)
        except KeyError:
            return _json({"error": "not_found"}, status=404)
        except DraftConflict:
            return _json({"error": "conflict", "message": "Черновик обновился. Проверь текущую версию."}, status=409)
        return _json({"results": results})

    async def home(request: web.Request, user, teacher) -> web.Response:
        """Сводка: «требует внимания» (оценки, счета, период) → «сегодня» (занятия дня) → «месяц» (зарплата)."""
        today = date.today().isoformat()
        period = current_period()
        month = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)
        today_lessons = sorted((ls for ls in month if ls.date == today), key=lambda x: x.recorded_at or "")
        submitted = await _submitted(teacher.teacher_id)
        prev = last_periods(2)[1]
        names = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        groups = await _group_names()
        today_items = [_lesson_brief(ls, teacher, groups, names) for ls in today_lessons]
        await _add_pay_counts(teacher, today_lessons, today_items)
        unrated = 0
        if diary_service is not None:
            athletes = await diary_service.athletes_for_teacher(teacher.teacher_id, is_admin=False)
            unrated = sum((await diary_service.unrated_counts([a.student_id for a in athletes])).values())
        can_bill = teacher.teacher_id in settings.billing_teacher_id_set
        rent = await _rent(teacher, period, month)
        prev_rent = await _rent(teacher, prev) if rent is not None and hall_rent.settled(prev) else None
        earned_month = await salary_service.total_for(teacher, period)
        payouts = await _payouts(teacher, period, earned_month)
        pilot = schedule_for(user)
        schedule_meta: dict = {}
        if pilot is not None:
            pending_today, completed_today, overview = await pilot.today_overview(schedule_pilot.moscow_today())
            schedule_meta = {
                "schedulePilot": True,
                "scheduleTodayPending": pending_today,
                "scheduleCompletedToday": completed_today,
                "scheduleOverview": overview,
            }
        return _json({
            "name": teacher.name, "today": today, "period": period, "prevPeriod": prev,
            **schedule_meta,
            # аренда зала (прямая оплата): сумма месяца рядом с зарплатой и напоминание за прошлый месяц
            "rentMonth": rent["accrued"] if rent else 0,
            "rentDue": {"period": prev, "rest": prev_rent["rest"]} if prev_rent and prev_rent["rest"] else None,
            # «требует внимания»: записи спортсменов без оценки; у педагога со счетами — остаток в своих группах
            "unrated": unrated,
            "bills": await bill_ctx.bills_summary(teacher, period) if can_bill else None,
            "inbox": await bill_ctx.queue_count(teacher, user),     # заявки об оплате (FULL_BILL_TEACHER_IDS)
            "heldCash": await bill_ctx.held_cash(teacher) if can_bill else 0,   # наличные на руках — передать админу
            "newChild": len(await bill_ctx.new_child_open(teacher)),            # заявки родителей на детей в своих группах
            # «сегодня»: занятия дня целиком, чтобы сводка не ходила за ними вторым запросом
            "lessonsToday": len(today_lessons),
            "todayLessons": today_items,
            "earnedToday": await salary_service.total_for(teacher, today),
            # «месяц»
            "lessonsMonth": len(month),
            "groupLessonsMonth": sum(1 for ls in month if ls.type == LessonType.GROUP),
            "individualLessonsMonth": sum(1 for ls in month if ls.type != LessonType.GROUP),
            "earnedMonth": earned_month,
            "paidMonth": (payouts or {}).get("paid", 0),         # выплачено за месяц (аванс) — в строке «Зарплата»
            # прямая оплата: школа не начисляет, но на сводке это не должно выглядеть нулём
            "directMonth": sum(direct_amount(ls, teacher) for ls in month),
            "directToday": sum(direct_amount(ls, teacher) for ls in today_lessons),
            "periodSubmit": settings.teacher_period_submit_enabled,
            "periodSubmitted": period in submitted, "prevSubmitted": prev in submitted,
            "canSubmit": settings.teacher_period_submit_enabled and LessonService.can_submit_period(date.today(), period),
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
        names = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        groups = await _group_names()
        submitted = await _submitted(teacher.teacher_id)
        ordered = sorted(rows, key=lambda x: (x.date, x.recorded_at or ""))
        out = [_lesson_brief(ls, teacher, groups, names) for ls in ordered]
        for item in out:
            item["locked"] = item["date"][:7] in submitted
        await _add_pay_counts(teacher, ordered, out)      # «оплатили N из M» в каждой строке
        return _json({
            "key": key, "isDay": len(key) == 10, "lessons": out,
            "earned": await salary_service.total_for(teacher, key),
        })

    async def lesson(request: web.Request, user, teacher) -> web.Response:
        ls = await lesson_repo.get_by_id(request.match_info["lid"])
        if ls is None or ls.teacher_id != teacher.teacher_id:
            return _json({"error": "not_found"}, status=404)
        g = await group_repo.get_by_id(ls.group_id) if ls.group_id else None
        students = {s.student_id: s for s in await student_repo.get_all(include_archived=True)}
        names = {sid: s.name for sid, s in students.items()}
        if ls.type == LessonType.GROUP:
            attendees = [{"studentId": e.student_id, "name": names.get(e.student_id, e.student_id),
                          "durationMin": e.duration_min, "amount": e.amount}
                         for e in parse_attendees(ls.attendees or "", default_duration=ls.duration_min)]
        else:
            ids = [i for i in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id) if i]
            slots = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
            # У прямой оплаты показываем долю каждого ученика — её платят педагогу лично.
            shares = ({row.student_id: row.amount
                       for row in build_billing_rows(ls, teacher, include_direct=True)}
                      if is_direct(ls, teacher) else {})
            attendees = [{"studentId": i, "name": names.get(i, n), "durationMin": ls.duration_min,
                          "amount": shares.get(i) if shares else None}
                         for i, n in zip(ids, slots, strict=False)]
        # Абонементная группа пишется без посещаемости — вместо пустого списка показываем состав
        # с оплатой абонемента за месяц занятия: педагог видит, кто из группы ещё не оплатил.
        cache: dict = {}
        ids, roster = await _lesson_student_ids(ls, g, cache)
        if roster:
            attendees = [{"studentId": sid, "name": names.get(sid, sid), "durationMin": ls.duration_min, "amount": None}
                         for sid in ids]
        statuses = await _pay_statuses(ls, teacher, g, [a["studentId"] for a in attendees], students, cache)
        for a, status in zip(attendees, statuses, strict=True):
            a["payStatus"] = status
        payable = [a for a in attendees if a["payStatus"]]
        return _json({
            "id": ls.lesson_id, "date": ls.date, "type": ls.type.value, "durationMin": ls.duration_min,
            "paidCount": sum(1 for a in payable if a["payStatus"] in ("paid", "sub_paid")),
            "payableCount": len(payable), "roster": roster,
            "groupId": ls.group_id or "", "groupName": lesson_group_title(ls, await _group_names(), g.name if g else ""),
            "groupMode": g.billing_mode.value if g else "",
            "freeLabel": free_attendee_label(g, has_amount_snapshots(ls.attendees)),
            "attendees": attendees, "recordedAt": ls.recorded_at,
            "earned": calc_earned(ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date),
            "direct": is_direct(ls, teacher), "directAmount": direct_amount(ls, teacher),
            "rent": lesson_rent(ls),
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

    # ── деньги и сдача периода ───────────────────────────────────────────
    async def _payouts(teacher, period: str, total: int) -> dict | None:
        """Сколько школа уже выплатила педагогу за месяц: аванс, остаток, премии (решение владельца 11.11.2026 —
        Контаревой платят авансом, а в кабинете этого видно не было). Те же строки `teacher_payouts`, что в карточке
        педагога у администратора. Руководителю (OWNER_TEACHER_IDS) не показываем — его зарплата остаётся в прибыли."""
        payout_repo, bonus_repo = dp.get("payout_repo"), dp.get("bonus_repo")
        if payout_repo is None or teacher.teacher_id in settings.owner_teacher_id_set:
            return None
        rows = sorted(await payout_repo.get_by_teacher_period(teacher.teacher_id, period), key=lambda p: p.paid_at)
        bonus_ids = ({b.payout_id for b in await bonus_repo.get_for_teacher_period(teacher.teacher_id, period)
                      if b.payout_id} if bonus_repo is not None else set())
        paid = sum(p.amount for p in rows)
        return {"paid": paid, "rest": max(total - paid, 0),
                "items": [{"amount": p.amount, "date": p.paid_at[:10], "bonus": p.payout_id in bonus_ids}
                          for p in rows]}

    async def stats(request: web.Request, user, teacher) -> web.Response:
        ym = request.query.get("ym") or current_period()
        lines = await salary_service.lines_for(teacher, ym)
        rows = await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, ym)
        submitted = await _submitted(teacher.teacher_id)
        names = {s.student_id: s.name for s in await student_repo.get_all(include_archived=True)}
        groups = await _group_names()
        by_id = {ls.lesson_id: ls for ls in rows}

        def label_for(ln) -> str:
            """У строк-занятий label пустой (в боте он не нужен) — подставляем, кто занимался."""
            if ln.kind == "share":
                # процент от абонементов: педагогу — только группа и его сумма, без начислений группы
                # (решение владельца 12.10.2026)
                return groups.get(ln.group_id, "Группа")
            if ln.label:
                return ln.label
            ls = by_id.get(ln.lesson_id or "")
            if ls is None:
                return ""
            if ls.type == LessonType.GROUP:
                return lesson_group_title(ls, groups, "Группа")
            who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
            return " + ".join(who) or names.get(ls.student_1_id or "", "Занятие")

        total = sum(ln.amount for ln in lines)
        return _json({
            "period": ym, "total": total,
            "payouts": await _payouts(teacher, ym, total),     # выплачено школой за месяц и остаток
            "groupLessons": sum(1 for ls in rows if ls.type == LessonType.GROUP),
            "individualLessons": sum(1 for ls in rows if ls.type != LessonType.GROUP),
            "submitted": ym in submitted,
            "lines": [{"date": ln.date, "kind": ln.kind, "label": label_for(ln), "groupId": ln.group_id,
                       "minutes": ln.minutes, "amount": ln.amount, "lessonId": ln.lesson_id,
                       "direct": is_direct(by_id[ln.lesson_id], teacher) if ln.lesson_id in by_id else False}
                      for ln in lines],
            # по ставкам: смена / группа / индивидуальные / процент — одна строка на правило, внутри занятия
            "summary": summary_dicts(summarize(lines, by_id, teacher, groups, names, for_teacher=True)),
            "direct": _direct_summary(rows, teacher, names),
            "rent": await _rent(teacher, ym, rows),
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
        if not settings.teacher_period_submit_enabled:
            return _json({"error": "disabled", "message": "Сдача периода отключена"}, status=403)
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
        ("GET", "/schedule-drafts", schedule_drafts),
        ("PUT", "/schedule-drafts/{mid}", schedule_save),
        ("POST", "/schedule-drafts/{mid}/attendance", schedule_attendance),
        ("GET", "/lessons", lessons), ("GET", "/lessons/{lid}", lesson), ("DELETE", "/lessons/{lid}", lesson_delete),
        ("GET", "/record/options", record_options_view), ("POST", "/record", record_create_view),
        *student_routes(dp, _own_group_ids, _group_names, _competitions, _lesson_brief, bill_ctx, bot),
        ("GET", "/stats", stats), ("GET", "/submit", submit_preview), ("POST", "/submit", submit),
        *diary_routes(diary_service, notifier, bot),
        *bill_routes,
        *homework_routes(dp, diary_service, notifier, _own_group_ids, _group_names, bot),   # задания группе (15.10.2026)
    ]
    for method, path, handler in routes:
        app.router.add_route(method, PREFIX + path, teacher_only(handler))

    # «Ждут решения» — заявки родителей об оплате (наличные, чеки) учеников своих групп;
    # решение то же, что у администратора (общий claim и замок оплат) — FULL_BILL_TEACHER_IDS
    def queue_only(handler):
        async def wrapped(request: web.Request, user, teacher) -> web.Response:
            if teacher.teacher_id not in settings.full_bill_teacher_id_set:
                return _json({"error": "forbidden"}, status=403)
            return await handler(request, user)
        return teacher_only(wrapped)

    from bot.api.admin_inbox import register_inbox_routes
    register_inbox_routes(app, dp, queue_only, PREFIX, bot, scope=bill_ctx.queue_scope)
    logger.info("Teacher API зарегистрирован: %d маршрутов на %s", len(routes), PREFIX)
