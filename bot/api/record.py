"""Mini App: запись занятия — общие функции для кабинета администратора (за педагога) и педагога.

Повторяет мастер бота (teacher/record_lesson): дата ≤ сегодня → тип (группа / пара /
несколько солистов в одном занятии / солисты по отдельности; у педагога с revenue-share
группой — «групповое / индивидуальное») → длительность → участники → LessonService.
Состояние мастера держит клиент; сервер отдаёт варианты (`options`) и создаёт занятия (`create`).
"""
from __future__ import annotations

import logging
from datetime import date

from bot.models.enums import GroupBillingMode, LessonType
from bot.utils.attendees import build_group_attendees_csv
from bot.utils.groups import hide_service_groups
from config.settings import settings

logger = logging.getLogger(__name__)

DURATIONS = [30, 35, 45, 60, 90, 120]
RSHARE_DURATIONS = [60, 120]
KINDS = ("group", "pair", "shared", "soloist", "rshare")


class RecordError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 400) -> None:
        super().__init__(message or code)
        self.code, self.status = code, status


async def _rshare_gid(dp, teacher_id: str) -> str:
    share = set(settings.revenue_share_group_map)
    if not share:
        return ""
    own = set(await dp["teacher_group_repo"].get_groups_for_teacher(teacher_id))
    return next(iter(own & share), "")


async def record_options(dp, teacher_id: str) -> dict:
    """Всё, что нужно мастеру для одного педагога: группы с составом, пары, солисты."""
    group_repo, branch_repo = dp["group_repo"], dp["branch_repo"]
    student_repo, student_group_repo, visibility = dp["student_repo"], dp["student_group_repo"], dp["visibility"]
    rshare = await _rshare_gid(dp, teacher_id)
    gids = hide_service_groups(await dp["teacher_group_repo"].get_groups_for_teacher(teacher_id))
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    students = {s.student_id: s for s in await student_repo.get_all()}
    membership = await student_group_repo.get_membership_map()
    groups = []
    for g in sorted(await group_repo.get_all(), key=lambda g: (branches.get(g.branch_id, ""), g.sort_order, g.name)):
        if g.group_id not in gids:
            continue
        roster = sorted((students[sid] for (sid, gid), m in membership.items() if gid == g.group_id and sid in students and m.is_active),
                        key=lambda s: s.name)
        groups.append({
            "id": g.group_id, "name": g.name, "branchId": g.branch_id, "branchName": branches.get(g.branch_id, ""),
            "mode": g.billing_mode.value, "priceFull": g.price_full, "priceShort": g.price_short,
            "durationFull": g.duration_full, "durationShort": g.duration_short,
            "roster": [{"id": s.student_id, "name": s.name, "tier": s.group_tier.value, "partnerId": s.partner_id} for s in roster],
        })
    mine = await visibility.students_for_teacher(teacher_id)
    mine_ids = {s.student_id for s in mine}
    pairs, seen = [], set()
    for s in sorted(mine, key=lambda s: s.name):
        if not s.partner_id or s.partner_id not in mine_ids:
            continue
        key = tuple(sorted([s.student_id, s.partner_id]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"aId": s.student_id, "aName": s.name, "bId": s.partner_id, "bName": students[s.partner_id].name})
    return {
        "teacherId": teacher_id, "today": date.today().isoformat(),
        "kinds": ["group", "rshare"] if rshare else ["group", "pair", "shared", "soloist"],
        "durations": DURATIONS, "rshareDurations": RSHARE_DURATIONS, "rshareGroupId": rshare,
        "groups": groups, "pairs": pairs,
        "students": [{"id": s.student_id, "name": s.name, "partnerId": s.partner_id} for s in sorted(mine, key=lambda s: s.name)],
    }


async def record_create(dp, teacher, body: dict, bypass_period_lock: bool) -> dict:
    """Создать занятие(я) по данным мастера. Ошибки — RecordError (код + текст для пользователя)."""
    lesson_service, group_repo, student_repo = dp["lesson_service"], dp["group_repo"], dp["student_repo"]
    kind, day, duration = body.get("kind"), body.get("date"), body.get("durationMin")
    if kind not in KINDS or not isinstance(day, str) or len(day) != 10:
        raise RecordError("bad_request", "Не указан тип или дата")
    try:
        if date.fromisoformat(day) > date.today():
            raise RecordError("future_date", "Дата в будущем — так нельзя")
    except ValueError:
        raise RecordError("bad_request", "Неверная дата") from None
    if kind == "rshare":
        duration = duration or 60
    if not isinstance(duration, int) or duration <= 0:
        raise RecordError("bad_request", "Не указана длительность")
    students = {s.student_id: s for s in await student_repo.get_all()}
    ids = [i for i in (body.get("studentIds") or []) if isinstance(i, str) and i in students]

    try:
        if kind in ("group", "rshare"):
            gid = body.get("groupId") if kind == "group" else await _rshare_gid(dp, teacher.teacher_id)
            group = await group_repo.get_by_id(gid) if gid else None
            if group is None:
                raise RecordError("bad_request", "Группа не выбрана")
            if kind == "rshare" and not (1 <= len(ids) <= 3):
                raise RecordError("bad_request", "Отметьте от 1 до 3 участниц")
            tiers = {sid: t for sid, t in (body.get("tiers") or {}).items() if t in ("short", "full")}
            if group.billing_mode == GroupBillingMode.PER_VISIT:
                tiers = {sid: tiers.get(sid, students[sid].group_tier.value) for sid in ids}
            csv = build_group_attendees_csv(group, ids, tiers)
            lesson = await lesson_service.create(
                teacher=teacher, lesson_type=LessonType.GROUP, lesson_date=day, duration_min=duration,
                attendees=csv, group_id=group.group_id, bypass_period_lock=bypass_period_lock,
            )
            return {"created": 1, "lessons": [lesson.lesson_id], "attendees": len(ids), "label": group.name}
        if kind == "pair":
            pairs = []
            for a_id in ids:
                a = students[a_id]
                if a.partner_id and a.partner_id in students:
                    pairs.append((a.student_id, a.name, a.partner_id, students[a.partner_id].name))
            if not pairs:
                raise RecordError("bad_request", "Не выбрана ни одна пара")
            lessons = await lesson_service.create_pair_batch(teacher=teacher, lesson_date=day, duration_min=duration,
                                                            pairs=pairs, bypass_period_lock=bypass_period_lock)
            return {"created": len(lessons), "lessons": [ls.lesson_id for ls in lessons],
                    "label": "; ".join(f"{a} ↔ {b}" for _, a, _, b in pairs)}
        if kind == "shared":
            if not (2 <= len(ids) <= 4):
                raise RecordError("bad_request", "Нужно от 2 до 4 солистов")
            picked = sorted((students[i] for i in ids), key=lambda s: s.student_id)
            lesson = await lesson_service.create_shared_individual(
                teacher=teacher, lesson_date=day, duration_min=duration,
                students=[(s.student_id, s.name) for s in picked], bypass_period_lock=bypass_period_lock,
            )
            return {"created": 1, "lessons": [lesson.lesson_id], "label": " + ".join(s.name for s in picked)}
        if not ids:
            raise RecordError("bad_request", "Не выбран ни один ученик")
        lessons = await lesson_service.create_soloist_batch(
            teacher=teacher, lesson_date=day, duration_min=duration,
            students=[(i, students[i].name) for i in ids], bypass_period_lock=bypass_period_lock,
        )
        return {"created": len(lessons), "lessons": [ls.lesson_id for ls in lessons],
                "label": ", ".join(students[i].name for i in ids)}
    except PermissionError as exc:
        raise RecordError("period_locked", str(exc), status=409) from exc
    except ValueError as exc:
        raise RecordError("conflict", str(exc), status=409) from exc
