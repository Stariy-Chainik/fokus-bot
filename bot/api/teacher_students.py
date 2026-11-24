"""Группы и ученики в кабинете педагога — `/api/teacher/groups*`, `/api/teacher/students*`.

Выделено из `bot/api/teacher.py`: список и карточка группы, поиск/добавление учеников,
переименование, детсадовская группа, классы ФТСАРР, дата рождения, уход, архив, перевод,
переключение 2/3 раза в неделю, занятия ученика и карточка ученика.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from aiohttp import web

from bot.models.enums import GroupBillingMode
from bot.services import activity, birthdays, dance_rank
from bot.services.kindergarten import clean_value, is_kindergarten_group, kindergarten_group_names
from bot.services.membership import leave_group, leave_options
from bot.services.new_child import same_person
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX
from bot.services.rosters import group_members
from bot.services.student_archive import archive_preview, archive_student
from bot.services.subscription_frequency import frequency_info, set_frequency
from bot.utils.attendees import parse_attendees
from bot.utils.dates import current_period, period_label
from bot.utils.groups import hide_service_groups
from bot.utils.notify import notify
from config.settings import settings

logger = logging.getLogger(__name__)


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _norm_name(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


def student_routes(
    dp: Any,
    own_group_ids: Callable,
    group_names: Callable,
    competitions: Callable,
    lesson_brief: Callable,
    bill_ctx: Any,
    bot: Any = None,
) -> list[tuple[str, str, Any]]:
    """Маршруты групп и учеников кабинета педагога (обёртку `teacher_only` ставит `teacher.py`)."""
    user_repo = dp["user_repo"]
    student_repo = dp["student_repo"]
    group_repo = dp["group_repo"]
    branch_repo = dp["branch_repo"]
    lesson_repo = dp["lesson_repo"]
    student_group_repo = dp["student_group_repo"]
    teacher_group_repo = dp["teacher_group_repo"]
    visibility = dp["visibility"]
    payment_service = dp["payment_service"]
    client_repo = dp["client_repo"]

    async def groups(request: web.Request, user, teacher) -> web.Response:
        gids = set(hide_service_groups(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id)))
        branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
        counts = {gid: len(await student_group_repo.get_students_for_group(gid)) for gid in gids}
        out = [{
            "id": g.group_id, "name": g.name,
            "branchId": g.branch_id, "branchName": branches.get(g.branch_id, ""),
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
        from bot.services.payment_breakdown import group_pay_block
        return _json({
            "id": g.group_id, "name": g.name, "mode": g.billing_mode.value,
            "priceFull": g.price_full, "priceShort": g.price_short,
            "kindergarten": is_kindergarten_group(g),
            "pay": await group_pay_block(dp, g, [s.student_id for s in members]),
            "students": [{"id": s.student_id, "name": s.name, "tier": s.group_tier.value,
                          "partnerId": s.partner_id or "", "kgroup": s.kindergarten_group} for s in members],
            "leaveOptions": [{"ym": ym, "label": label} for ym, label in leave_options()],
            "pairs": pairs, "soloists": [{"id": s.student_id, "name": s.name} for s in solo],
        })

    async def students_search(request: web.Request, user, teacher) -> web.Response:
        """Поиск по базе для «Добавить ученика»: имя и группы — чтобы педагог не завёл вторую карточку."""
        q = _norm_name(request.query.get("q") or "")
        if len(q) < 3:
            return _json({"students": []})
        names = await group_names()
        groups_of: dict[str, list] = {}
        for m in await student_group_repo.get_all():
            if not m.left_period:
                groups_of.setdefault(m.student_id, []).append(names.get(m.group_id, m.group_id))
        found = [{"id": s.student_id, "name": s.name, "groups": groups_of.get(s.student_id, [])}
                 for s in await student_repo.get_all() if q in _norm_name(s.name)]
        found.sort(key=lambda x: x["name"])
        return _json({"students": found[:10]})

    async def member_add(request: web.Request, user, teacher) -> web.Response:
        """«Добавить ученика» в свою группу: существующий ученик (`studentId`) или новая карточка (`name`)."""
        gid = request.match_info["gid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        if gid not in await own_group_ids(teacher):
            return _json({"error": "not_found"}, status=404)
        g = await group_repo.get_by_id(gid)
        if g is None:
            return _json({"error": "not_found"}, status=404)
        sid = (body or {}).get("studentId")
        name = " ".join(str((body or {}).get("name") or "").split())
        if sid:
            s = await student_repo.get_by_id(sid)
            if s is None:
                return _json({"error": "not_found"}, status=404)
            if sid in await student_group_repo.get_students_for_group(gid):
                return _json({"error": "already", "message": "Ученик уже в этой группе"}, status=409)
            await student_group_repo.add(sid, gid)
            created = False
        else:
            if not 3 <= len(name) <= 60:
                return _json({"error": "bad_request", "message": "Введите фамилию и имя"}, status=400)
            same = [x for x in await student_repo.get_all() if same_person(x.name, name)]
            if same and not bool((body or {}).get("force")):
                gnames = await group_names()
                groups_of: dict[str, list[str]] = {}
                for m in await student_group_repo.get_all():
                    if m.is_active:
                        groups_of.setdefault(m.student_id, []).append(str(gnames.get(m.group_id, m.group_id)))
                return _json({"error": "duplicate", "message": "Такой ученик уже есть в базе — добавьте его в группу",
                              "students": [{"id": x.student_id, "name": x.name, "groups": groups_of.get(x.student_id, [])}
                                           for x in same]}, status=409)
            s = (await dp["student_service"].create_with_group(name, gid)).student
            created = True
        if bot is not None:
            text = (f"➕ Педагог {teacher.name} добавил ученика в группу\nУченик: {s.name}"
                    f"{' (новая карточка)' if created else ''}\nГруппа: {g.name}")
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()], text)
        logger.info("Mini App: педагог %s добавил %s в %s (%s)", teacher.teacher_id, s.student_id, gid,
                    "новый" if created else "существующий")
        return _json({"ok": True, "studentId": s.student_id, "name": s.name, "created": created})

    async def student_rename(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = None
        name = " ".join(str((body or {}).get("name") or "").split())
        if not 3 <= len(name) <= 60 or not any(ch.isalpha() for ch in name):
            return _json({"error": "bad_request", "message": "Введите фамилию и имя"}, status=400)
        if name == s.name:
            return _json({"ok": True, "name": name, "unchanged": True})
        same = [x for x in await student_repo.get_all()
                if x.student_id != sid and _norm_name(x.name) == _norm_name(name)]
        if same and not bool((body or {}).get("force")):
            return _json({"error": "duplicate", "message": "Ученик с таким именем уже есть в базе",
                          "students": [{"id": x.student_id, "name": x.name} for x in same]}, status=409)
        if not await student_repo.update_name(sid, name, actor=user.tg_id):
            return _json({"error": "not_found"}, status=404)
        if bot is not None:
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()],
                         f"✏️ Педагог {teacher.name} исправил имя ученика\nБыло: {s.name}\nСтало: {name}")
        logger.info("Mini App: педагог %s переименовал %s: %s → %s", teacher.teacher_id, sid, s.name, name)
        return _json({"ok": True, "name": name})

    async def student_kgroup(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        all_groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        if not await kindergarten_group_names(sid, student_group_repo, all_groups):
            return _json({"error": "not_kindergarten", "message": "Ученик не в садовой группе"}, status=400)
        try:
            body = await request.json()
        except Exception:
            body = None
        value = clean_value((body or {}).get("value"))
        if value is None:
            return _json({"error": "too_long", "message": "Не длиннее 40 символов"}, status=400)
        ok = await student_repo.update_kindergarten_group(sid, value, actor=user.tg_id, who=f"педагог {teacher.name}")
        return _json({"ok": ok, "value": value}, status=200 if ok else 404)

    async def student_dance(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        s = await student_repo.get_by_id(sid)
        if s is None or not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        all_groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        if not (await dance_rank.in_ballroom_group(sid, student_group_repo, all_groups) or dance_rank.has_profile(s)):
            return _json({"error": "not_ballroom", "message": "Ученик не в группе бальных танцев"}, status=400)
        try:
            body = await request.json()
        except Exception:
            body = None
        status, payload = await dance_rank.apply_update(student_repo, s, body or {}, actor=user.tg_id,
                                                        who=f"педагог {teacher.name}")
        return _json(payload, status=status)

    async def student_birth(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        s = await student_repo.get_by_id(sid)
        if s is None or not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = None
        value = birthdays.parse_birth_date((body or {}).get("birthDate"))
        if value is None:
            return _json({"error": "bad_date", "message": "Дата рождения — в формате ДД.ММ.ГГГГ, не в будущем"}, status=400)
        await student_repo.update_birth_date(sid, value, actor=user.tg_id, who=f"педагог {teacher.name}")
        s.birth_date = value
        all_groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        return _json({"ok": True, **birthdays.view(s, await dance_rank.in_ballroom_group(sid, student_group_repo, all_groups))})

    async def member_leave(request: web.Request, user, teacher) -> web.Response:
        gid, sid = request.match_info["gid"], request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        left = str((body or {}).get("leftPeriod") or "")
        if left not in {ym for ym, _ in leave_options()}:
            return _json({"error": "bad_request", "message": "Выберите месяц ухода"}, status=400)
        if gid not in await own_group_ids(teacher) or sid not in await student_group_repo.get_students_for_group(gid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        g = await group_repo.get_by_id(gid)
        if s is None or g is None:
            return _json({"error": "not_found"}, status=404)
        ledger = await payment_service.compute_ledger_map(since_period=settings.debtors_since_period or None)
        debt = sum(a - p for (st_id, _k, _ym), (a, p) in ledger.items() if st_id == sid and a > p)
        result = await leave_group(sid, gid, left, group_repo, student_group_repo)
        if result == "missing":
            return _json({"error": "not_found"}, status=404)
        if result == "removed":
            await activity.record(activity.STUDENT, f"Ученик {sid} убран из группы {gid} педагогом {teacher.name}",
                                  actor=user.tg_id, ref=sid)
        if bot is not None:
            from bot.utils.format import format_money
            text = (f"👋 Педагог {teacher.name}: ученик уходит из группы\nУченик: {s.name}\nГруппа: {g.name}\n"
                    + (f"С месяца: {period_label(left)} (абонемент дальше не начисляется)" if result == "marked"
                       else "Убран из состава (оплата по посещениям — история сохранена)")
                    + (f"\nНеоплачено {format_money(debt)} — у ушедшего в должниках не считается" if debt > 0 else "\nДолгов нет."))
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()], text)
        logger.info("Mini App: педагог %s — ученик %s ушёл из %s с %s (%s, неоплачено %d)",
                    teacher.teacher_id, sid, gid, left, result, debt)
        return _json({"ok": True, "result": result, "leftPeriod": left, "debt": debt})

    def _archive_deps() -> dict:
        return {"student_repo": student_repo, "student_group_repo": student_group_repo, "group_repo": group_repo,
                "payment_service": payment_service, "since": settings.debtors_since_period or None}

    async def student_archive_preview(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        own = await own_group_ids(teacher)
        if not own & set(await student_group_repo.get_groups_for_student(sid)):
            return _json({"error": "not_found"}, status=404)
        p = await archive_preview(sid, **_archive_deps())
        if p is None:
            return _json({"error": "not_found"}, status=404)
        d = p.as_dict()
        d["groups"] = [g for g in d["groups"] if g["id"] in own]
        d["others"] = len(p.groups) - len(d["groups"])
        return _json(d)

    async def student_archive(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            body = None
        own = await own_group_ids(teacher)
        current = set(await student_group_repo.get_groups_for_student(sid))
        if not own & current:
            return _json({"error": "not_found"}, status=404)
        r = await archive_student(sid, **_archive_deps(), client_repo=client_repo, actor=user.tg_id,
                                  who=f"педагог {teacher.name}", confirm_debt=bool((body or {}).get("confirmDebt")))
        if isinstance(r, str):
            if r == "not_found":
                return _json({"error": "not_found"}, status=404)
            if r == "already":
                return _json({"error": "already", "message": "Ученик уже в архиве"}, status=409)
            p = await archive_preview(sid, **_archive_deps())
            return _json({"error": "debt", "debt": p.debt.as_dict() if p else {}}, status=409)
        if bot is not None:
            from bot.utils.format import format_money
            others = {g for g, _n in r.groups} - own
            text = (f"📦 Педагог {teacher.name}: ученик ушёл из школы\nУченик: {r.name}\n"
                    f"Снят с групп: {', '.join(n for _g, n in r.groups) or 'не состоял в группах'}\n"
                    f"Родителей отвязано: {r.parents}" + (f" · партнёр {r.partner} отвязан" if r.partner else "")
                    + (f"\nНеоплачено {format_money(r.debt.total)} — у ушедшего в должниках не считается"
                       if r.debt.total else "\nДолгов нет.")
                    + "\nКарточка в архиве, вернуть — в карточке ученика.")
            admins = {u.tg_id for u in await user_repo.get_admins()}
            if others:
                tids = {tg.teacher_id for tg in await teacher_group_repo.get_all() if tg.group_id in others}
                admins |= {u.tg_id for u in await user_repo.get_all()
                           if u.teacher_id in tids and u.tg_id != user.tg_id}
            await notify(bot, sorted(admins), text)
        logger.info("Mini App: педагог %s — ученик %s в архиве", teacher.teacher_id, sid)
        return _json(r.as_dict())

    async def student_transfer_options(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        own, names = await own_group_ids(teacher), await group_names()
        current = set(await student_group_repo.get_groups_for_student(sid))
        s = await student_repo.get_by_id(sid)
        if s is None or not own & current:
            return _json({"error": "not_found"}, status=404)
        live = {g.group_id: g for g in await group_repo.get_all()}

        def by_name(gid: str) -> str:
            return str(names.get(gid, gid))
        return _json({
            "student": {"id": sid, "name": s.name},
            "from": [{"id": gid, "name": names.get(gid, gid),
                      "subscription": gid in live and live[gid].billing_mode == GroupBillingMode.SUBSCRIPTION}
                     for gid in sorted(own & current, key=by_name)],
            "to": [{"id": gid, "name": names.get(gid, gid), "mode": live[gid].billing_mode.value}
                   for gid in sorted((own - current) & set(live), key=by_name)],
            "leaveOptions": [{"ym": ym, "label": label} for ym, label in leave_options()],
        })

    async def student_transfer(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        src, dst = str((body or {}).get("fromGroupId") or ""), str((body or {}).get("toGroupId") or "")
        left = str((body or {}).get("leftPeriod") or current_period())
        own = await own_group_ids(teacher)
        if src == dst or src not in own or dst not in own:
            return _json({"error": "not_found"}, status=404)
        current = set(await student_group_repo.get_groups_for_student(sid))
        if src not in current:
            return _json({"error": "not_found"}, status=404)
        if dst in current:
            return _json({"error": "already", "message": "Ученик уже в этой группе"}, status=409)
        s = await student_repo.get_by_id(sid)
        g_from, g_to = await group_repo.get_by_id(src), await group_repo.get_by_id(dst)
        if s is None or g_from is None or g_to is None or g_to.archived:
            return _json({"error": "not_found"}, status=404)
        if g_from.billing_mode == GroupBillingMode.SUBSCRIPTION and left not in {ym for ym, _ in leave_options()}:
            return _json({"error": "bad_request", "message": "Выберите месяц ухода"}, status=400)
        ledger = await payment_service.compute_ledger_map(since_period=settings.debtors_since_period or None)
        debt = sum(a - p for (st_id, _k, _ym), (a, p) in ledger.items() if st_id == sid and a > p)
        await student_group_repo.add(sid, dst)
        result = await leave_group(sid, src, left, group_repo, student_group_repo)
        if result == "missing":
            return _json({"error": "not_found"}, status=404)
        await activity.record(activity.STUDENT, f"Ученик {sid} переведён педагогом {teacher.name}: {src} → {dst}"
                              + (f" (ушёл с {left})" if result == "marked" else ""), actor=user.tg_id, ref=sid)
        if bot is not None:
            from bot.utils.format import format_money
            text = (f"🔄 Педагог {teacher.name} перевёл ученика в другую группу\nУченик: {s.name}\n"
                    f"Из: {g_from.name}\nВ: {g_to.name}\n"
                    + (f"Из старой — с {period_label(left)} (абонемент дальше не начисляется)" if result == "marked"
                       else "Из старой убран сразу (оплата по посещениям — история сохранена)")
                    + (f"\nНеоплачено {format_money(debt)} — остаётся в счёте" if debt > 0 else "\nДолгов нет."))
            await notify(bot, [u.tg_id for u in await user_repo.get_admins()], text)
        logger.info("Mini App: педагог %s перевёл %s: %s → %s (%s, с %s, неоплачено %d)",
                    teacher.teacher_id, sid, src, dst, result, left, debt)
        return _json({"ok": True, "result": result, "leftPeriod": left, "debt": debt, "toName": g_to.name})

    async def student_frequency(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if teacher.teacher_id not in settings.senior_teacher_id_set:
            return _json({"error": "forbidden"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "bad_request"}, status=400)
        gid = body.get("groupId")
        if gid not in await own_group_ids(teacher) or gid not in await student_group_repo.get_groups_for_student(sid):
            return _json({"error": "not_found"}, status=404)
        ok = await set_frequency(dp["subscription_override_repo"], sid, gid, body.get("times"),
                                 str(body.get("since") or ""), user.tg_id)
        return _json({"ok": True} if ok else {"error": "bad_request"}, status=200 if ok else 400)

    async def student_lessons(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        own = set(await teacher_group_repo.get_groups_for_teacher(teacher.teacher_id))
        full = await bill_ctx.full_bill(teacher, sid)

        def keep(ls) -> bool:
            return full or (any(g in own for g in ls.group_ids) if ls.group_id else ls.teacher_id == teacher.teacher_id)
        from bot.api.student_lessons import month_lessons
        d = await month_lessons(dp, sid, request.query.get("ym") or current_period(), keep,
                                money=teacher.teacher_id in settings.billing_teacher_id_set)
        for x in d["lessons"]:
            x["mine"] = x["teacherId"] == teacher.teacher_id
        return _json({"student": {"id": sid, "name": s.name}, "all": full, **d})

    async def student(request: web.Request, user, teacher) -> web.Response:
        sid = request.match_info["sid"]
        if not await visibility.is_visible(teacher.teacher_id, sid):
            return _json({"error": "not_found"}, status=404)
        s = await student_repo.get_by_id(sid)
        if s is None:
            return _json({"error": "not_found"}, status=404)
        groups_map = await group_names()
        partner = await student_repo.get_by_id(s.partner_id) if s.partner_id else None
        ym = request.query.get("ym") or current_period()
        mine = [ls for ls in await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, ym)
                if sid in (ls.student_1_id, ls.student_2_id, ls.student_3_id, ls.student_4_id)
                or sid in [e.student_id for e in parse_attendees(ls.attendees or "")]]
        names = {x.student_id: x.name for x in await student_repo.get_all(include_archived=True)}
        gids = await student_group_repo.get_groups_for_student(sid)
        own_visible = await own_group_ids(teacher)
        tariffs = []
        if teacher.teacher_id in settings.senior_teacher_id_set:
            own = await own_group_ids(teacher)
            for gid in gids:
                g = await group_repo.get_by_id(gid) if gid in own else None
                info = await frequency_info(dp["subscription_override_repo"], sid, g) if g else {}
                if info:
                    tariffs.append({"id": gid, "name": g.name, **info})
        subscriptions = []
        editable = await bill_ctx.sub_edit_group_ids(teacher)
        if editable & set(gids):
            ledgers = await payment_service.ledger_for(s, ym, sync=False)
            for gid in gids:
                led = ledgers.get(f"{SUBSCRIPTION_KEY_PREFIX}{gid}") if gid in editable else None
                if led is not None:
                    subscriptions.append({"groupId": gid, "name": groups_map.get(gid, gid),
                                          "total": led.accrued, "paid": led.paid})
        all_groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        debt = await bill_ctx.student_debt(teacher, sid) if teacher.teacher_id in settings.billing_teacher_id_set else None
        ballroom = any(dance_rank.is_ballroom_group(all_groups.get(g)) for g in gids)
        return _json({
            "id": s.student_id, "name": s.name, "tier": s.group_tier.value, "debt": debt,
            "kindergarten": any(is_kindergarten_group(all_groups.get(g)) for g in gids), "kgroup": s.kindergarten_group,
            "dance": dance_rank.profile(s, ballroom),
            "birth": birthdays.view(s, ballroom),
            "competitions": await competitions(sid),
            "partner": {"id": partner.student_id, "name": partner.name} if partner else None,
            "groups": [groups_map.get(g, g) for g in gids if g in own_visible], "tariffs": tariffs,
            "groupIds": [g for g in gids if g in own_visible],
            "subscriptions": subscriptions,
            "period": ym,
            "lessons": [lesson_brief(ls, teacher, groups_map, names)
                        for ls in sorted(mine, key=lambda x: x.date)],
        })

    return [
        ("GET", "/groups", groups),
        ("GET", "/groups/{gid}", group),
        ("GET", "/students/search", students_search),      # раньше /students/{sid}: иначе «search» примут за id
        ("GET", "/students/{sid}", student),
        ("PATCH", "/students/{sid}", student_rename),
        ("PUT", "/students/{sid}/kgroup", student_kgroup),
        ("PUT", "/students/{sid}/dance", student_dance),
        ("PUT", "/students/{sid}/birth", student_birth),
        ("GET", "/students/{sid}/lessons", student_lessons),
        ("PUT", "/students/{sid}/frequency", student_frequency),
        ("GET", "/students/{sid}/transfer", student_transfer_options),
        ("POST", "/students/{sid}/transfer", student_transfer),
        ("PUT", "/groups/{gid}/members/{sid}/leave", member_leave),
        ("GET", "/students/{sid}/archive", student_archive_preview),
        ("POST", "/students/{sid}/archive", student_archive),
        ("POST", "/groups/{gid}/members", member_add),
    ]
