"""Mini App: запись занятия — общие функции для кабинета администратора (за педагога) и педагога.

Повторяет мастер бота (teacher/record_lesson): дата ≤ сегодня → тип (группа / пара /
несколько солистов в одном занятии / солисты по отдельности; у педагога с revenue-share
группой — «групповое / индивидуальное») → длительность → участники → LessonService.
Состояние мастера держит клиент; сервер отдаёт варианты (`options`) и создаёт занятия (`create`).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import date

from bot.models.enums import GroupBillingMode, LessonType
from bot.utils.attendees import TRIAL_TIER, build_group_attendees_csv, parse_attendees, serialize_attendees
from bot.services.lesson_service import SameDaySoloError
from bot.utils.groups import hide_service_groups, merged_title
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
        attendance = g.billing_mode == GroupBillingMode.PER_VISIT or g.group_id in settings.attendance_group_id_set
        roster = sorted((students[sid] for (sid, gid), m in membership.items() if gid == g.group_id and sid in students and m.is_active),
                        key=lambda s: s.name)
        groups.append({
            "id": g.group_id, "name": g.name, "branchId": g.branch_id, "branchName": branches.get(g.branch_id, ""),
            "mode": g.billing_mode.value, "priceFull": g.price_full, "priceShort": g.price_short,
            "attendance": attendance,
            # с какими своими группами можно объединить занятие (MERGEABLE_GROUPS; нужна отметка посещения)
            "mergeWith": sorted(settings.mergeable_group_map.get(g.group_id, set()) & gids) if attendance else [],
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


async def _merge_groups(dp, teacher_id: str, group, merge_ids, ids: list[str]) -> list[tuple]:
    """Объединённое занятие: [(группа, {её ученики среди отмеченных})] для групп из `mergeGroupIds`.

    Объединять можно только свои группы из MERGEABLE_GROUPS. Ученик, который есть и в основной группе,
    платит по её цене — в объединённую он не попадает. Группа без отмеченных детей не объединяется."""
    if not merge_ids:
        return []
    allowed = settings.mergeable_group_map.get(group.group_id, set())
    own = set(await dp["teacher_group_repo"].get_groups_for_teacher(teacher_id))
    if not isinstance(merge_ids, list) or any(not isinstance(g, str) or g not in allowed or g not in own
                                              for g in merge_ids):
        raise RecordError("bad_request", "Эти группы нельзя объединить")
    membership = await dp["student_group_repo"].get_membership_map()

    def member(sid: str, gid: str) -> bool:
        m = membership.get((sid, gid))
        return m is not None and m.is_active

    taken = {sid for sid in ids if member(sid, group.group_id)}
    out = []
    for gid in dict.fromkeys(merge_ids):
        g = await dp["group_repo"].get_by_id(gid)
        mine = {sid for sid in ids if sid not in taken and member(sid, gid)}
        if g is None or not mine:
            continue
        taken |= mine
        out.append((g, mine))
    return out


REPEAT_WINDOW_SEC = 30.0
_recent: dict[tuple, tuple[float, asyncio.Future]] = {}


def _request_key(teacher_id: str, body: dict) -> tuple:
    """Что считается тем же самым сохранением. Кабинет шлёт номер нажатия `requestId` (каждое «Сохранить» — свой):
    повтор того же номера — сетевой дубль, а новое нажатие — новое занятие, даже если оно такое же (второе соло
    в тот же день, 10.10.2026). Без номера (старая версия кабинета) — педагог, тип, дата, длительность, группа,
    ученики, тарифы."""
    rid = body.get("requestId")
    if isinstance(rid, str) and 8 <= len(rid) <= 64:
        return (teacher_id, "rid", rid)
    ids = body.get("studentIds") if isinstance(body.get("studentIds"), list) else []
    tiers = body.get("tiers") if isinstance(body.get("tiers"), dict) else {}
    amounts = body.get("amounts") if isinstance(body.get("amounts"), dict) else {}
    tiers = {**tiers, **{f"amt:{k}": v for k, v in amounts.items()}}        # своя сумма — другое сохранение
    return (teacher_id, body.get("kind"), body.get("date"), body.get("durationMin"), body.get("groupId") or "",
            tuple(sorted(str(i) for i in ids)), tuple(sorted((str(k), str(v)) for k, v in tiers.items())))


async def record_create(dp, teacher, body: dict, bypass_period_lock: bool) -> dict:
    """Создать занятие(я) по данным мастера; повтор того же запроса в течение 30 с не создаёт занятия заново.

    Случай 10.10.2026: два одинаковых «Сохранить» с iPhone с разницей в секунду записали обе пары дважды.
    Второй запрос ждёт первый и получает его результат с `repeat: True`; если первый упал — пробует сам.
    """
    key = _request_key(teacher.teacher_id, body)
    while True:
        now = time.monotonic()
        for k in [k for k, (ts, _) in _recent.items() if now - ts > REPEAT_WINDOW_SEC]:
            _recent.pop(k, None)
        hit = _recent.get(key)
        if hit is None:
            break
        result = await asyncio.shield(hit[1])
        if result is not None:
            logger.info("Запись занятия: повтор запроса %s за %.1f с — новые занятия не создаются",
                        result.get("lessons"), time.monotonic() - hit[0])
            return {**result, "repeat": True}
        # первый запрос не удался (его ключ уже снят) — пробуем записать сами
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _recent[key] = (time.monotonic(), fut)
    try:
        result = await _record_create(dp, teacher, body, bypass_period_lock)
    except BaseException:
        _recent.pop(key, None)
        fut.set_result(None)
        raise
    fut.set_result(result)
    return result


RSHARE_MAX_AMOUNT = 100_000


def _rshare_amounts(csv: str | None, amounts, duration: int) -> str | None:
    """Индивидуальное в техгруппе REVENUE_SHARE (Яковлева): своя сумма с каждой ученицы вместо цены группы
    (решение владельца 24.10.2026). Сумма — в снимке attendees, поэтому счёт родителю и 50 % педагогу
    (`calc_earned` по сумме attendees) считаются от неё."""
    if not csv or not isinstance(amounts, dict):
        return csv
    entries = parse_attendees(csv, default_duration=duration)
    for i, e in enumerate(entries):
        raw = amounts.get(e.student_id)
        if raw is None or raw == "":
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise RecordError("bad_request", "Сумма — целое число рублей") from None
        if not 0 <= value <= RSHARE_MAX_AMOUNT:
            raise RecordError("bad_request", f"Сумма от 0 до {RSHARE_MAX_AMOUNT} ₽")
        entries[i] = replace(e, amount=value)
    return serialize_attendees(entries)


async def _ensure_visible(dp, teacher_id: str, group_id: str, ids: list) -> None:
    """Ученица не из групп педагога (нашли по фамилии во всей школе) — добавляем в его техгруппу индивидуальных,
    чтобы он видел её счёт и мог отметить оплату (решение владельца 24.10.2026)."""
    visibility, sg_repo = dp["visibility"], dp["student_group_repo"]
    for sid in ids:
        if not await visibility.is_visible(teacher_id, sid):
            await sg_repo.add(sid, group_id)
            logger.info("Индивидуальное: %s добавлена в %s педагога %s (не из его групп)", sid, group_id, teacher_id)


async def _record_create(dp, teacher, body: dict, bypass_period_lock: bool) -> dict:
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
            tiers = {sid: t for sid, t in (body.get("tiers") or {}).items() if t in ("short", "full", TRIAL_TIER)}
            merged = await _merge_groups(dp, teacher.teacher_id, group, body.get("mergeGroupIds"), ids)
            parts: list[str] = []
            for g, gids_ in [(group, [i for i in ids if not any(i in m for _g, m in merged)]),
                             *[(g, [i for i in ids if i in m]) for g, m in merged]]:
                if not gids_ and g is not group:
                    continue
                g_tiers = ({sid: tiers.get(sid, students[sid].group_tier.value) for sid in gids_}
                           if g.billing_mode == GroupBillingMode.PER_VISIT else tiers)
                parts.append(build_group_attendees_csv(g, gids_, g_tiers) or "")
            csv = ",".join(p for p in parts if p) or None
            if kind == "rshare":
                csv = _rshare_amounts(csv, body.get("amounts"), duration)
                await _ensure_visible(dp, teacher.teacher_id, group.group_id, ids)
            lesson = await lesson_service.create(
                teacher=teacher, lesson_type=LessonType.GROUP, lesson_date=day, duration_min=duration,
                attendees=csv, group_id=group.group_id, bypass_period_lock=bypass_period_lock,
                merged_groups=[g.group_id for g, _m in merged],
            )
            label = merged_title([group.name, *(g.name for g, _m in merged)])
            return {"created": 1, "lessons": [lesson.lesson_id], "attendees": len(ids), "label": label}
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
            allow_same_day=bool(body.get("confirmSameDay")),
        )
        return {"created": len(lessons), "lessons": [ls.lesson_id for ls in lessons],
                "label": ", ".join(students[i].name for i in ids)}
    except PermissionError as exc:
        raise RecordError("period_locked", str(exc), status=409) from exc
    except SameDaySoloError as exc:
        # второе соло в тот же день — кабинет спросит и повторит с confirmSameDay
        raise RecordError("same_day", str(exc), status=409) from exc
    except ValueError as exc:
        raise RecordError("conflict", str(exc), status=409) from exc
