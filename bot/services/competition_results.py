"""Турниры ФТСАРР у наших учеников (решение владельца 10.11.2026): обход результатов и привязка к карточкам.

Раз в день (`FTSARR_SYNC_TIME`, по умолчанию 07:30 МСК) бот берёт из календаря ФТСАРР турниры текущего и прошлого
месяца по регионам `FTSARR_REGIONS` (50 — Московская область, 77 — Москва), у ещё не обойдённых скачивает
результаты всех групп (`bot/services/ftsarr.py`) и ищет **только учеников бота** (решение владельца: пары других
тренеров под вывеской клуба не берём): фамилия и имя совпадают с карточкой (уменьшительные — «Коля» = «Николай»,
«Саша» = «Александр», буква ё и номер сада не мешают), а пара — из клуба «Фокус» или у нашего педагога.
Находки пишутся в лист `competition_results` (`CompetitionResultRepository.upsert_many`, без дублей), турнир
отмечается в `competition_scans`, администраторам и тренерам пары уходит сообщение. Результаты видны в карточке
ученика у администратора и педагога, в дневнике спортсмена и у родителя; у администратора — экран «🏆 Турниры».
История за прошлые месяцы — `scripts/import_ftsarr_results.py`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import settings

from . import activity
from .ftsarr import Competition, Competitor, FtsarrClient, GroupResults, program_of

logger = logging.getLogger(__name__)

# уменьшительное → полные имена (в нижнем регистре, без ё); в карточках дети часто записаны уменьшительно
DIMINUTIVES: dict[str, set[str]] = {
    "саша": {"александр", "александра"}, "шура": {"александр", "александра"}, "коля": {"николай"},
    "соня": {"софья", "софия"}, "ира": {"ирина"}, "настя": {"анастасия"}, "катя": {"екатерина"},
    "лена": {"елена"}, "даша": {"дарья"}, "варя": {"варвара"}, "маша": {"мария"}, "лера": {"валерия"},
    "таня": {"татьяна"}, "женя": {"евгений", "евгения"}, "дима": {"дмитрий"}, "миша": {"михаил"},
    "тима": {"тимофей", "тимур"}, "вика": {"виктория"}, "лиза": {"елизавета"}, "юля": {"юлия"},
    "оля": {"ольга"}, "аня": {"анна"}, "ваня": {"иван"}, "петя": {"петр"}, "сережа": {"сергей"},
    "андрюша": {"андрей"}, "кирюша": {"кирилл"}, "степа": {"степан"}, "тема": {"артем", "артемий"},
    "леша": {"алексей"}, "макс": {"максим"}, "гоша": {"георгий"}, "жора": {"георгий"}, "гриша": {"григорий"},
    "паша": {"павел"}, "рома": {"роман"}, "даня": {"даниил", "данил", "данила"}, "слава": {"вячеслав", "ярослав", "станислав"},
    "стас": {"станислав"}, "вова": {"владимир"}, "володя": {"владимир"}, "ника": {"вероника", "николь"},
    "ксюша": {"ксения"}, "люба": {"любовь"}, "надя": {"надежда"}, "света": {"светлана"}, "наташа": {"наталья", "наталия"},
    "валя": {"валентина", "валентин"}, "мира": {"мирослава"}, "марго": {"маргарита"}, "рита": {"маргарита"},
    "уля": {"ульяна"}, "тася": {"таисия"}, "тая": {"таисия"}, "вася": {"василиса", "василий"}, "лиля": {"лилия"},
    "геля": {"ангелина"}, "лина": {"ангелина", "полина", "алина"}, "лева": {"лев"}, "сеня": {"арсений", "семен"},
    "сема": {"семен"}, "федя": {"федор"}, "сава": {"савелий"}, "мотя": {"матвей"}, "витя": {"виктор"},
    "костя": {"константин"}, "леня": {"леонид"}, "сева": {"всеволод"}, "валера": {"валерий"}, "веня": {"вениамин"},
    "эля": {"эльвира", "элина"}, "яна": {"янина"}, "ася": {"анастасия"}, "нюра": {"анна"}, "элла": {"элеонора"},
    "толя": {"анатолий"}, "боря": {"борис"}, "егорка": {"егор"}, "влад": {"владислав"}, "владик": {"владислав"},
    "вадик": {"вадим"}, "илюша": {"илья"}, "тимоша": {"тимофей"}, "захарка": {"захар"},
    # полные имена с двумя написаниями
    "данил": {"даниил", "данила"}, "данила": {"даниил", "данил"}, "даниил": {"данил", "данила"},
    "софия": {"софья"}, "софья": {"софия"}, "наталья": {"наталия"}, "наталия": {"наталья"},
    "артем": {"артемий"}, "артемий": {"артем"},
}


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def person_words(name: str) -> tuple[str, str]:
    """(фамилия, имя) из «Фамилия Имя [номер сада]» карточки; имени может не быть."""
    words = [w for w in _norm(name).split() if not w.isdigit()]
    return (words[0] if words else "", words[1] if len(words) > 1 else "")


def first_name_matches(ours: str, theirs: str) -> bool:
    """Имя карточки (возможно уменьшительное) против полного имени в базе ФТСАРР."""
    a, b = _norm(ours), _norm(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    return b in DIMINUTIVES.get(a, set()) or a in DIMINUTIVES.get(b, set())


class StudentIndex:
    """Ученики бота по фамилии — для быстрого поиска пары из результатов."""

    def __init__(self, students: list) -> None:
        self._by_surname: dict[str, list] = {}
        for s in students:
            surname, _ = person_words(s.name)
            if surname:
                self._by_surname.setdefault(surname, []).append(s)

    def find(self, last_name: str, first_name: str):
        candidates = self._by_surname.get(_norm(last_name), [])
        hits = [s for s in candidates if first_name_matches(person_words(s.name)[1], first_name)]
        if len(hits) > 1:
            logger.warning("ФТСАРР: «%s %s» подходит нескольким ученикам: %s — пропускаю",
                           last_name, first_name, ", ".join(s.student_id for s in hits))
            return None
        return hits[0] if hits else None


def teacher_keys(teachers: list) -> dict[str, str]:
    """{«фамилия имя» → teacher_id} — тренер из результатов сопоставляется с педагогом бота."""
    out = {}
    for t in teachers:
        surname, first = person_words(t.name)
        if surname and first:
            out[f"{surname} {first}"] = t.teacher_id
    return out


def is_ours(c: Competitor, teachers: dict[str, str]) -> bool:
    """Пара клуба «Фокус» или у нашего педагога — чужие тёзки из других клубов не берём."""
    if "фокус" in _norm(c.club):
        return True
    return any(" ".join(person_words(coach)) in teachers for coach in c.coaches)


def rows_from_group(comp: Competition, g: GroupResults, index: StudentIndex, teachers: dict[str, str]) -> list:
    """Строки результатов для учеников бота из одной группы турнира (по строке на ученика)."""
    from bot.repositories.competition_repo import CompetitionResult
    out = []
    total = len(g.competitors)
    program = program_of(g.group_name)
    for c in g.competitors:
        if not c.athletes:
            continue
        matched = [(a, index.find(a.last_name, a.first_name)) for a in c.athletes]
        if not any(s is not None for _, s in matched):
            continue
        if not is_ours(c, teachers):
            logger.info("ФТСАРР: %s — ученик бота, но пара из клуба «%s» (%s), пропускаю",
                        " / ".join(a.name for a in c.athletes), c.club, ", ".join(c.coaches))
            continue
        for a, s in matched:
            if s is None:
                continue
            partner = next((x for x, _ in matched if x is not a), None)
            partner_student = next((ps for x, ps in matched if x is not a), None)
            out.append(CompetitionResult(
                result_id="", student_id=s.student_id, competition_id=comp.id, competition_name=comp.name,
                date=comp.start_date, city=comp.city, group_name=g.group_name, program=program,
                place=c.place, total=total, partner_name=partner.name if partner else "",
                partner_id=partner_student.student_id if partner_student else "",
                dance_class=a.dance_class, class_position=a.class_position, class_total=a.class_total,
                points=a.points, coaches="|".join(c.coaches),
            ))
    return out


@dataclass
class ScanOutcome:
    competition: Competition
    groups: int = 0
    rows: list = field(default_factory=list)
    added: list = field(default_factory=list)
    updated: int = 0


async def scan_competition(client: FtsarrClient, comp: Competition, index: StudentIndex,
                           teachers: dict[str, str]) -> ScanOutcome:
    """Все группы турнира → строки результатов учеников бота (без записи)."""
    outcome = ScanOutcome(comp)
    groups = await client.schedule(comp.id)
    outcome.groups = len(groups)
    for gid, gname, _count in groups:
        try:
            g = await client.group_results(comp.id, gid, gname)
        except Exception as exc:                       # одна группа не должна ронять обход турнира
            logger.warning("ФТСАРР: турнир %s, группа %s «%s»: %s", comp.id, gid, gname, exc)
            continue
        outcome.rows.extend(rows_from_group(comp, g, index, teachers))
    return outcome


def _regions() -> list[str]:
    raw = (settings.ftsarr_regions or "").strip().lower()
    if raw in ("", "all", "все"):
        return []
    return [r.strip() for r in raw.replace("|", ",").split(",") if r.strip()]


def months_back(n: int, today: date | None = None) -> list[str]:
    """Текущий месяц и n−1 предыдущих: ['2026-11', '2026-10']."""
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for _ in range(max(1, n)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


@dataclass
class SyncStats:
    competitions: int = 0
    scanned: int = 0
    skipped: int = 0
    pending: int = 0
    rows: int = 0
    added: int = 0
    updated: int = 0
    outcomes: list = field(default_factory=list)


_sync_lock = asyncio.Lock()
GIVE_UP_DAYS = 30            # турнир без групп в результатах перестаём проверять через месяц после окончания


def sync_running() -> bool:
    return _sync_lock.locked()


async def sync(dp, months: list[str], regions: list[str] | None = None, *, client: FtsarrClient | None = None,
               force: bool = False, bot=None, notify: bool = True, log=None) -> SyncStats:
    """Обойти турниры месяцев `months` (YYYY-MM): новые — скачать, записать, сообщить. Повторный обход
    обойдённых турниров — только с `force`. Одновременно идёт не больше одного обхода."""
    stats = SyncStats()
    own_client = client is None
    client = client or FtsarrClient()
    regions = _regions() if regions is None else regions
    say = log or (lambda msg: logger.info(msg))
    async with _sync_lock:
        try:
            students = await dp["student_repo"].get_all()                   # без архивных — только кто в боте
            index = StudentIndex(students)
            teachers = teacher_keys(await dp["teacher_repo"].get_all())
            scan_repo, result_repo = dp["competition_scan_repo"], dp["competition_result_repo"]
            scanned = set() if force else await scan_repo.scanned_ids()
            today = date.today().isoformat()
            ids: list[int] = []
            for ym in months:
                y, m = int(ym[:4]), int(ym[5:7])
                ids += await client.calendar_ids(y, m, regions)
            ids = list(dict.fromkeys(ids))
            stats.competitions = len(ids)
            for cid in ids:
                if cid in scanned:
                    stats.skipped += 1
                    continue
                comp = await client.competition(cid)
                if comp is None:
                    continue
                if not comp.is_completed or (comp.end_date and comp.end_date >= today):
                    stats.pending += 1                                       # ещё идёт — вернёмся завтра
                    continue
                outcome = await scan_competition(client, comp, index, teachers)
                if outcome.groups == 0 and comp.end_date >= (date.today() - timedelta(days=GIVE_UP_DAYS)).isoformat():
                    stats.pending += 1                                       # результаты ещё не выложили — не отмечаем
                    say(f"Турнир {cid} «{comp.name}»: групп в результатах нет, вернёмся позже")
                    continue
                outcome.added, outcome.updated = await result_repo.upsert_many(outcome.rows)
                await scan_repo.mark(cid, comp.name, comp.start_date, comp.city, outcome.groups, len(outcome.rows))
                stats.scanned += 1
                stats.rows += len(outcome.rows)
                stats.added += len(outcome.added)
                stats.updated += outcome.updated
                stats.outcomes.append(outcome)
                say(f"Турнир {cid} «{comp.name}» ({comp.city}, {comp.start_date}): групп {outcome.groups}, "
                    f"наших строк {len(outcome.rows)}, новых {len(outcome.added)}")
                if outcome.rows:
                    await activity.record("competition", f"Турнир «{comp.name}» {comp.start_date}: выступлений наших "
                                          f"{len(outcome.rows)}, новых {len(outcome.added)}", ref=f"comp:{cid}")
                if outcome.added and bot is not None and notify:
                    await notify_results(bot, dp, comp, outcome.added)
        finally:
            if own_client:
                await client.close()
    return stats


def view(rows: list, names: dict[str, str] | None = None) -> list[dict]:
    """Строки для экранов: свежие первыми; партнёр — по карточке бота, если он в боте."""
    names = names or {}
    out = []
    for r in sorted(rows, key=lambda r: (r.date, r.competition_id, r.group_name), reverse=True):
        out.append({
            "id": r.result_id, "competitionId": r.competition_id, "competition": r.competition_name,
            "date": r.date, "city": r.city, "group": r.group_name, "program": r.program,
            "place": r.place, "total": r.total,
            "studentId": r.student_id, "student": names.get(r.student_id, r.student_id),
            "partner": names.get(r.partner_id) or r.partner_name, "partnerId": r.partner_id or None,
            "class": r.dance_class, "classPosition": r.class_position, "classTotal": r.class_total,
            "coaches": r.coach_list,
        })
    return out


def competitions_view(rows: list, names: dict[str, str] | None = None) -> list[dict]:
    """Экран «Турниры»: турниры свежие первыми, внутри — пары один раз (оба партнёра из бота — одна строка)."""
    names = names or {}
    comps: dict[int, dict] = {}
    seen: set[tuple] = set()
    for v in view(rows, names):
        c = comps.setdefault(v["competitionId"], {"id": v["competitionId"], "name": v["competition"],
                                                   "date": v["date"], "city": v["city"], "pairs": []})
        members = [{"id": v["studentId"], "name": v["student"], "class": v["class"],
                    "classPosition": v["classPosition"], "classTotal": v["classTotal"]}]
        if v["partner"]:
            members.append({"id": v["partnerId"], "name": v["partner"], "class": "", "classPosition": 0, "classTotal": 0})
        key = (v["competitionId"], v["group"], frozenset(m["id"] or m["name"] for m in members))
        if key in seen:                                   # партнёр тоже в боте — его строка дополняет класс
            pair = next(p for p in c["pairs"] if p["_key"] == key)
            for m in pair["members"]:
                if (m["id"] or m["name"]) in (v["studentId"], v["student"]):
                    m.update({"id": v["studentId"], "name": v["student"], "class": v["class"],
                              "classPosition": v["classPosition"], "classTotal": v["classTotal"]})
            continue
        seen.add(key)
        c["pairs"].append({"_key": key, "group": v["group"], "program": v["program"], "place": v["place"],
                           "total": v["total"], "members": members, "coaches": v["coaches"]})
    out = []
    for c in comps.values():
        c["pairs"].sort(key=lambda p: (p["group"], p["place"]))
        for p in c["pairs"]:
            p.pop("_key", None)
        out.append(c)
    return out


def _pair_lines(rows: list, names: dict[str, str]) -> list[str]:
    """«• Васькин Саша / Дикая Эмилия — Юниоры-1, ST: 59 из 180» — пара в группе один раз."""
    seen, lines = set(), []
    for r in sorted(rows, key=lambda r: (r.group_name, r.place)):
        me = names.get(r.student_id, r.student_id)
        partner = names.get(r.partner_id) or r.partner_name
        key = (r.group_name, frozenset({me, partner}))
        if key in seen:
            continue
        seen.add(key)
        who = f"{me} / {partner}" if partner else me
        cls = f" ({r.dance_class}: {r.class_position} из {r.class_total})" if r.dance_class and r.class_total else ""
        lines.append(f"• {who} — {r.group_name}: <b>{r.place} из {r.total}</b>{cls}")
    return lines


def results_text(comp: Competition, rows: list, names: dict[str, str]) -> str:
    from bot.utils.dates import format_date_display
    return (f"🏆 Результаты турнира «{comp.name}»\n{comp.city} · {format_date_display(comp.start_date)}\n\n"
            + "\n".join(_pair_lines(rows, names)))


async def notify_results(bot, dp, comp: Competition, rows: list) -> int:
    """Администраторам — все наши пары турнира, педагогу — его пары (он среди тренеров пары)."""
    from bot.screens.adapters import to_aiogram_markup
    from bot.screens.types import webapp
    try:
        names = {s.student_id: s.name for s in await dp["student_repo"].get_all(include_archived=True)}
        teachers = teacher_keys(await dp["teacher_repo"].get_all())
        users = await dp["user_repo"].get_all()
        tg_of = {u.teacher_id: u.tg_id for u in users if u.teacher_id}
        admins = [u.tg_id for u in users if u.is_admin]
        kb = None
        if settings.miniapp_url:                        # администратору откроется экран «Турниры» месяца
            sep = "&" if "?" in settings.miniapp_url else "?"
            kb = to_aiogram_markup([[webapp("📱 Открыть кабинет", f"{settings.miniapp_url}{sep}open=comp&ym={comp.start_date[:7]}")]])
        by_teacher: dict[str, list] = {}
        for r in rows:
            for coach in r.coach_list:
                tid = teachers.get(" ".join(person_words(coach)))
                if tid:
                    by_teacher.setdefault(tid, []).append(r)
        sent = 0
        targets: dict[int, list] = {tg: rows for tg in admins}
        for tid, items in by_teacher.items():
            tg = tg_of.get(tid)
            if tg and tg not in targets:
                targets[tg] = items
        for tg, items in targets.items():
            try:
                await bot.send_message(tg, results_text(comp, items, names), reply_markup=kb)
                sent += 1
            except Exception as exc:
                logger.warning("Результаты турнира %s: %s не получил сообщение: %s", comp.id, tg, exc)
        return sent
    except Exception as exc:                            # уведомление вспомогательное
        logger.warning("Результаты турнира %s: уведомления не ушли: %s", comp.id, exc)
        return 0


async def run(dp, bot, interval_sec: int = 60) -> None:
    """Фоновая задача: раз в день в FTSARR_SYNC_TIME обходит турниры текущего и прошлого месяца."""
    tz = ZoneInfo(settings.reminder_tz)
    done: set[str] = set()
    while True:
        try:
            if settings.ftsarr_sync_enabled:
                now = datetime.now(tz)
                key = now.date().isoformat()
                if key not in done and now.time() >= datetime.strptime(settings.ftsarr_sync_time, "%H:%M").time():
                    done.add(key)
                    stats = await sync(dp, months_back(settings.ftsarr_lookback_months, now.date()), bot=bot)
                    logger.info("ФТСАРР обход %s: турниров %d, обойдено %d, пропущено %d, идут %d, строк %d (+%d, ~%d)",
                                key, stats.competitions, stats.scanned, stats.skipped, stats.pending, stats.rows,
                                stats.added, stats.updated)
                    if len(done) > 7:                   # не копить даты
                        done.intersection_update({key, (now.date() - timedelta(days=1)).isoformat()})
        except Exception as exc:
            logger.exception("ФТСАРР обход: %s", exc)
        await asyncio.sleep(interval_sec)
