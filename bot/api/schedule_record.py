"""Запись итогов черновика через штатный журнал занятий."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.api.record import RecordError, record_create, record_options
from bot.models.enums import LessonType
from bot.services.schedule_pilot import DraftConflict, SchedulePilot
from bot.utils.attendees import TRIAL_TIER

logger = logging.getLogger(__name__)
_MOSCOW = ZoneInfo("Europe/Moscow")


def _journal_match(lesson, slot: dict, day: str) -> bool:
    if lesson.date != day:
        return False
    if slot["kind"] == "group":
        return lesson.type == LessonType.GROUP and slot["groupId"] in lesson.group_ids
    participants = {x for x in (lesson.student_1_id, lesson.student_2_id, lesson.student_3_id, lesson.student_4_id) if x}
    return lesson.type == LessonType.INDIVIDUAL and bool(participants) and participants == set(slot["studentIds"])


async def draft_day(dp, teacher, pilot: SchedulePilot, mid: int, body: dict) -> list[dict]:
    actions = body.get("actions")
    if (not isinstance(actions, list) or not 1 <= len(actions) <= 100
            or any(not isinstance(a, dict) or not isinstance(a.get("time"), str)
                   or a.get("status") not in ("held", "cancelled", "planned") for a in actions)):
        raise ValueError("Выбери занятия и их статусы")
    async with pilot.lock:
        drafts = await pilot.drafts(await dp["student_repo"].get_all())
        plan = next((d for d in drafts if d["id"] == mid), None)
        if plan is None:
            raise KeyError(mid)
        if body.get("revision") != plan["revision"] or body.get("sourceRevision") != plan["sourceRevision"]:
            raise DraftConflict
        by_time = {s["time"]: s for s in plan["slots"] if s["kind"] != "free"}
        times = {a["time"] for a in actions}
        if len(times) != len(actions) or not times <= by_time.keys():
            raise ValueError("Проверь выбранные занятия")
        if not plan["revision"]:
            # Дальше изменения Telegram могут обновить источник, но не рабочий план.
            await pilot.save_edit(mid, 0, plan["sourceRevision"], None)
        statuses = await pilot.outcomes(mid)
        options = None
        results = []
        for action in actions:
            slot = by_time[action["time"]]
            if slot["kind"] == "group" and action["status"] == "held" and options is None:
                options = await record_options(dp, teacher.teacher_id)
            result = await _slot_outcome(dp, teacher, pilot, mid, plan["date"], slot, action, statuses, options)
            results.append({"time": slot["time"], **result})
        return results


def _record_body(slot: dict, day: str, action: dict, options: dict | None) -> dict:
    duration = slot["duration"]
    if not duration:
        raise RecordError("duration", "Укажи длительность занятия")
    if day > datetime.now(_MOSCOW).date().isoformat():
        raise RecordError("future_date", "Занятие можно отметить в день проведения или позже")
    if slot["kind"] == "individual":
        ids = slot["studentIds"]
        if not ids:
            raise RecordError("participants", "Выбери ученика или пару")
        return {"kind": "shared" if len(ids) == 2 else "soloist", "date": day,
                "studentIds": ids, "durationMin": duration, "confirmSameDay": action.get("allowNew") is True}
    group = next((g for g in (options or {}).get("groups", []) if g["id"] == slot["groupId"]), None)
    if not group:
        raise RecordError("group", "Выбери группу")
    ids, tiers = [], {}
    if group["attendance"]:
        if action.get("attendanceConfirmed") is not True:
            raise RecordError("attendance", "Отметь, кто был на групповом занятии")
        ids = action.get("studentIds", [])
        roster = {s["id"] for s in group["roster"]}
        if not isinstance(ids, list) or any(not isinstance(s, str) or s not in roster for s in ids) or len(set(ids)) != len(ids):
            raise RecordError("attendance", "Проверь список присутствовавших")
        submitted = action.get("tiers") or {}
        if not isinstance(submitted, dict):
            raise RecordError("attendance", "Проверь тарифы присутствовавших")
        tiers = {sid: submitted[sid] for sid in ids if submitted.get(sid) in ("full", "short", TRIAL_TIER)}
    return {"kind": "group", "date": day, "groupId": group["id"], "studentIds": ids, "tiers": tiers, "durationMin": duration}


async def _slot_outcome(dp, teacher, pilot, mid, day, slot, action, statuses, options) -> dict:
    time, target = slot["time"], action["status"]
    status, lesson_id, pending_body = statuses.get(time, ("planned", "", None))
    if status == "held":
        return {"status": status, "lessonId": lesson_id}
    if target != "held":
        if status == "pending":
            return {"status": status, "error": "Сначала проверь запись в журнале"}
        await pilot.set_outcome(mid, time, target)
        statuses[time] = (target, "", None)
        return {"status": target}
    if status == "cancelled":
        return {"status": status, "error": "Сначала верни занятие в план"}
    lesson_repo = dp["lesson_repo"]
    try:
        if status == "pending":
            # Сбой после записи в Google мог оставить неопределённый результат. Сверяемся с живым листом.
            refresh = getattr(lesson_repo, "refresh", None)
            if refresh is not None:
                await refresh()
            if await lesson_repo.get_by_id(lesson_id):
                await pilot.set_outcome(mid, time, "held", lesson_id)
                statuses[time] = ("held", lesson_id, None)
                return {"status": "held", "lessonId": lesson_id}
            body = json.loads(pending_body)
        else:
            matches = [ls for ls in await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, day[:7])
                       if _journal_match(ls, slot, day)]
            linked = {ident for state, ident, _ in statuses.values() if state == "held"}
            selected = action.get("lessonId")
            if selected:
                if not isinstance(selected, str) or selected in linked or not any(ls.lesson_id == selected for ls in matches):
                    raise RecordError("lesson", "Выбери подходящую запись из журнала")
                await pilot.set_outcome(mid, time, "held", selected)
                statuses[time] = ("held", selected, None)
                return {"status": "held", "lessonId": selected}
            if matches and action.get("allowNew") is not True:
                return {"status": status, "code": "existing", "error": "В журнале уже есть похожее занятие",
                        "matches": [{"id": ls.lesson_id, "recordedAt": ls.recorded_at, "durationMin": ls.duration_min}
                                    for ls in matches if ls.lesson_id not in linked]}
            body = _record_body(slot, day, action, options)
            digest = hashlib.sha256(f"{pilot.chat_id}/{pilot.tg_id}/{teacher.teacher_id}/{mid}/{time}".encode()).hexdigest()
            lesson_id = f"LES-SCH-{digest[:20]}"
            body["requestId"] = lesson_id
            await pilot.set_outcome(mid, time, "pending", lesson_id, body)
            statuses[time] = ("pending", lesson_id, json.dumps(body))
        result = await record_create(dp, teacher, body, bypass_period_lock=False, lesson_id=lesson_id)
    except RecordError as exc:
        if statuses.get(time, ("", "", None))[0] == "pending":
            await pilot.set_outcome(mid, time, "planned")
            statuses.pop(time, None)
        return {"status": "planned", "code": exc.code, "error": str(exc)}
    except Exception:
        logger.exception("Не удалось проверить запись черновика %s (%s)", mid, time)
        state = statuses.get(time, ("planned", "", None))[0]
        return {"status": state, "error": "Журнал временно недоступен. Повтори проверку этой строки."}
    actual_id = result["lessons"][0]
    await pilot.set_outcome(mid, time, "held", actual_id)
    statuses[time] = ("held", actual_id, None)
    return {"status": "held", "lessonId": actual_id}
