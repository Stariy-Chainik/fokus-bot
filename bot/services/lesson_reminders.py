"""Напоминания педагогам отметить занятие — по расписанию групп (лист group_schedule).

В `TEACHER_REMINDER_TIME` (по умолчанию 21:00 МСК) бот сравнивает расписание дня с отмеченными
занятиями: у группы по расписанию N занятий, отмечено M → не хватает N − M. Педагогу — сообщение
с кнопкой «Отметить занятие». В `ADMIN_DIGEST_TIME` (10:00) администраторам — сводка за вчера по тому,
что так и не отметили. Кому напоминать: педагог слота; если не указан — кто вёл группу последние
4 недели; если никто — все педагоги группы. Отправка раз в день: отметка в ленте «События»
(`ref=remind:…`), поэтому перезапуск бота не шлёт повторно.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import settings

from . import activity

logger = logging.getLogger(__name__)
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@dataclass
class Missing:
    group_id: str
    group_name: str
    slots: list          # слоты дня (ScheduleSlot)
    recorded: int        # отмечено занятий группы за день
    teacher_ids: list = field(default_factory=list)

    @property
    def missing(self) -> int:
        return len(self.slots) - self.recorded

    @property
    def times(self) -> str:
        return ", ".join(f"{s.start}–{s.end}" for s in self.slots)


def missing_for_day(day: date, slots: list, lessons: list, groups: dict, group_teachers: dict) -> list[Missing]:
    """Чистый расчёт: группы, у которых по расписанию на `day` занятий больше, чем отмечено.

    slots — все ScheduleSlot; lessons — занятия (нужны date, group_id, teacher_id); groups — {gid: Group}
    (архивные пропускаются); group_teachers — {gid: [teacher_id]} из teacher_groups.
    """
    iso, wd = day.isoformat(), day.isoweekday()
    since = (day - timedelta(days=28)).isoformat()
    today_slots: dict[str, list] = {}
    for s in slots:
        g = groups.get(s.group_id)
        if s.weekday == wd and g is not None and not getattr(g, "archived", False):
            today_slots.setdefault(s.group_id, []).append(s)
    out = []
    for gid, gslots in today_slots.items():
        recorded = sum(1 for ls in lessons if ls.group_id == gid and ls.date == iso)
        if recorded >= len(gslots):
            continue
        teachers = [s.teacher_id for s in gslots if s.teacher_id]
        if not teachers:
            recent: dict[str, int] = {}
            for ls in lessons:
                if ls.group_id == gid and since <= ls.date < iso:
                    recent[ls.teacher_id] = recent.get(ls.teacher_id, 0) + 1
            teachers = sorted(recent, key=lambda t: -recent[t]) or list(group_teachers.get(gid, []))
        out.append(Missing(gid, groups[gid].name, sorted(gslots, key=lambda s: s.start), recorded,
                           list(dict.fromkeys(teachers))))
    return sorted(out, key=lambda m: m.slots[0].start)


async def _collect(dp, day: date) -> list[Missing]:
    slots = await dp["group_schedule_repo"].get_all()
    if not slots:
        return []
    groups = {g.group_id: g for g in await dp["group_repo"].get_all(include_archived=True)}
    group_teachers: dict[str, list] = {}
    for tg in await dp["teacher_group_repo"].get_all():
        group_teachers.setdefault(tg.group_id, []).append(tg.teacher_id)
    return missing_for_day(day, slots, await dp["lesson_repo"].get_all(), groups, group_teachers)


async def _already(ref: str) -> bool:
    repo = activity._repo
    if repo is None:
        return False
    try:
        return any(e.ref == ref for e in await repo.since((date.today() - timedelta(days=2)).isoformat()))
    except Exception:
        return False


async def send_teacher_reminders(dp, bot, day: date) -> int:
    """Педагогам — по одному сообщению со всеми неотмеченными группами дня."""
    from bot.screens.adapters import to_aiogram_markup
    from bot.screens.types import cb, webapp
    missing = await _collect(dp, day)
    users = await dp["user_repo"].get_all()
    tg_of = {u.teacher_id: u.tg_id for u in users if u.teacher_id}
    by_teacher: dict[str, list] = {}
    for m in missing:
        for t in m.teacher_ids:
            by_teacher.setdefault(t, []).append(m)
    rows = [[cb("✏️ Отметить занятие", "teacher:record_lesson")]]
    if settings.miniapp_url:
        rows.append([webapp("📱 Открыть кабинет", settings.miniapp_url)])
    sent = 0
    for tid, items in by_teacher.items():
        tg = tg_of.get(tid)
        if not tg:
            continue
        lines = [f"• {m.group_name} — {m.times}" + (f" (отмечено {m.recorded} из {len(m.slots)})" if m.recorded else "")
                 for m in items]
        text = "📝 Сегодня по расписанию не отмечены занятия:\n\n" + "\n".join(lines) + \
               "\n\nОтметьте, пожалуйста, — по ним считаются зарплата и счета родителям."
        try:
            await bot.send_message(tg, text, reply_markup=to_aiogram_markup(rows))
            sent += 1
        except Exception as exc:
            logger.warning("Напоминание педагогу %s не ушло: %s", tid, exc)
    teachers = {t.teacher_id: t.name for t in await dp["teacher_repo"].get_all()}
    for m in missing:
        await activity.record("lesson", f"Не отмечено по расписанию: {m.group_id} · {day.isoformat()} · {m.times}"
                              f" · напомнили: {', '.join(teachers.get(t, t) for t in m.teacher_ids) or 'некому'}",
                              ref=f"remind:{day.isoformat()}:{m.group_id}")
    await activity.record("lesson", f"Напоминания педагогам за {day.isoformat()}: групп {len(missing)}, сообщений {sent}",
                          ref=f"remind:{day.isoformat()}:teachers")
    logger.info("Напоминания педагогам %s: групп %d, сообщений %d", day, len(missing), sent)
    return sent


async def send_admin_digest(dp, bot, day: date) -> int:
    missing = await _collect(dp, day)
    teachers = {t.teacher_id: t.name for t in await dp["teacher_repo"].get_all()}
    if missing:
        lines = [f"• {m.group_name} — {m.times}, не хватает {m.missing} · "
                 f"{', '.join(teachers.get(t, t).split()[0] for t in m.teacher_ids) or 'педагог не назначен'}" for m in missing]
        text = f"📋 Не отмечены занятия за {WD[day.weekday()]} {day.strftime('%d.%m')}:\n\n" + "\n".join(lines)
    else:
        text = None
    sent = 0
    if text:
        for admin in await dp["user_repo"].get_admins():
            try:
                await bot.send_message(admin.tg_id, text)
                sent += 1
            except Exception as exc:
                logger.warning("Сводка админу %s не ушла: %s", admin.tg_id, exc)
    await activity.record("lesson", f"Сводка неотмеченных за {day.isoformat()}: групп {len(missing)}",
                          ref=f"remind:{day.isoformat()}:admins")
    return sent


def _at(hhmm: str) -> tuple[int, int]:
    h, m = (hhmm or "0:0").split(":")
    return int(h), int(m)


async def run(dp, bot, interval_sec: int = 60) -> None:
    """Фоновая задача: раз в минуту проверяет, не пора ли напомнить (время — по REMINDER_TZ)."""
    tz = ZoneInfo(settings.reminder_tz)
    done: set[str] = set()
    while True:
        try:
            if settings.lesson_reminders_enabled:
                now = datetime.now(tz)
                today = now.date()
                if now.time() >= datetime.strptime(settings.teacher_reminder_time, "%H:%M").time():
                    ref = f"remind:{today.isoformat()}:teachers"
                    if ref not in done and not await _already(ref):
                        await send_teacher_reminders(dp, bot, today)
                    done.add(ref)
                if now.time() >= datetime.strptime(settings.admin_digest_time, "%H:%M").time():
                    y = today - timedelta(days=1)
                    ref = f"remind:{y.isoformat()}:admins"
                    if ref not in done and not await _already(ref):
                        await send_admin_digest(dp, bot, y)
                    done.add(ref)
        except Exception as exc:
            logger.exception("Напоминания педагогам: %s", exc)
        await asyncio.sleep(interval_sec)
