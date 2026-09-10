"""Синхронизация посещений Яковлевой из Google-таблицы «Посещения» в бота.

Источник правды — таблица (только чтение); бот приводится в соответствие:
- составы групп (сад — все из списка; Боброво — только не-пробные),
- занятия по датам с посещаемостью (пробное «П» — бесплатно, amount=0),
- индивидуальные (лист 2): 1800 ₽ с участницы, «2» = два занятия.
Идемпотентно: повторный запуск ничего не дублирует.

Запуск:  .venv/bin/python scripts/sync_yakovleva_attendance.py [YYYY-MM]  (по умолчанию — текущий месяц)
"""
from __future__ import annotations
import asyncio, json, os, re, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import gspread  # noqa: E402
from google.oauth2.service_account import Credentials  # noqa: E402
from config.settings import settings  # noqa: E402
from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from bot.repositories.lesson_repo import LessonRepository  # noqa: E402
from bot.repositories.student_repo import StudentRepository  # noqa: E402
from bot.repositories.student_group_repo import StudentGroupRepository  # noqa: E402
from bot.models.entities import Lesson  # noqa: E402
from bot.models.enums import LessonType  # noqa: E402
from bot.utils.ids import generate_lesson_id  # noqa: E402
from bot.utils.dates import now_str  # noqa: E402

SOURCE_ID = "1qO4NRnluWD_P4iAIEgDSE5OHHIY0ahGV92hK2_ak0zc"
TEACHER = ("TCH-0011", "Яковлева Олеся")
# Группы бота: (group_id, длительность занятия, режим)
GROUPS = {
    "sad":    ("GRP-0008", 60,  "per_visit"),  # ЮЖНАЯ БИТЦА (сад)
    "nach":   ("GRP-0021", 60,  "sub"),        # Боброво: Начальная подготовка
    "sred":   ("GRP-0022", 120, "sub"),        # Боброво: Средняя
    "sport":  ("GRP-0023", 120, "sub"),        # Боброво: Спортивная (в листе «Старшая»)
}
# Заголовок секции листа (нижний регистр) → ключ GROUPS; если не нашли — по составу
BLOCK_TITLES = {"(сад)": "sad", "начальная": "nach", "средняя": "sred", "старшая": "sport", "спортивная": "sport"}
INDIVIDUAL_GROUP, INDIVIDUAL_PRICE = "GRP-0020", 1800
PER_VISIT_FULL, PER_VISIT_SHORT = (850, 60), (500, 35)


def _key(n: str) -> str:
    return " ".join(n.lower().replace("ё", "е").split()[:2])


def _section_for(rows, i) -> str:
    """Ближайший непустой заголовок над строкой дат (напр. «НАЧАЛЬНАЯ ПОДГОТОВКА», «ЮЖНАЯ БИТЦА (сад)»)."""
    for k in range(i - 1, max(-1, i - 12), -1):
        v = rows[k][0].strip()
        if v and not v[0].isdigit():
            return v.lower()
    return ""


def parse_blocks(rows, month: str):
    mm = int(month[5:7])
    pat = re.compile(rf"^(\d{{1,2}})\.{mm:02d}\.?$")
    for i, r in enumerate(rows):
        cols = [(j, int(pat.match(c.strip()).group(1))) for j, c in enumerate(r) if pat.match(c.strip())]
        if not cols:
            continue
        # заголовки секции: строки над датами до предыдущего блока (не глубже 6 строк)
        titles = " ".join(rows[k][0].strip().lower() for k in range(max(0, i - 6), i))
        roster, marks = [], {}
        for rr in rows[i + 1:]:
            c0 = rr[0].strip()
            name = re.sub(r"\s*\d+$", "", rr[1].strip()) if len(rr) > 1 else ""
            if not c0.isdigit():
                if name or c0:
                    continue
                break
            if not name:
                continue
            roster.append(name)
            for j, day in cols:
                v = rr[j].strip() if j < len(rr) else ""
                if v:
                    marks.setdefault(day, []).append((name, v))
        yield titles, roster, marks


async def main() -> None:
    month = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y-%m")
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    src = gspread.authorize(creds).open_by_key(SOURCE_ID)
    sc = SheetsClient(settings)
    stu, sg, les = (StudentRepository(sc, settings.sheet_students),
                    StudentGroupRepository(sc, settings.sheet_student_groups),
                    LessonRepository(sc, settings.sheet_lessons))
    all_students = [s for s in await stu.get_all() if s.name and s.name != "None"]
    by_key = {_key(s.name): s for s in all_students}
    by_id = {s.student_id: s for s in all_students}
    changes = 0

    async def ensure(name: str):
        nonlocal changes
        s = by_key.get(_key(name))
        if s is None:
            s = await stu.add(name); by_key[_key(name)] = s; by_id[s.student_id] = s; changes += 1
            print("  + ученик", s.student_id, name)
        return s

    existing = {(l.date, l.group_id): l for l in await les.get_by_teacher_and_period(TEACHER[0], month)}
    ids = await les.get_existing_ids()

    async def add_lesson(day: int, gid: str, dur: int, att: str):
        nonlocal changes
        date_s = f"{month}-{day:02d}"
        lid = generate_lesson_id(ids); ids.append(lid)
        lesson = Lesson(lesson_id=lid, teacher_id=TEACHER[0], teacher_name=TEACHER[1], type=LessonType.GROUP,
                        student_1_id=None, student_1_name=None, student_2_id=None, student_2_name=None,
                        date=date_s, duration_min=dur, earned=0, recorded_at=now_str(), updated_at=now_str(),
                        attendees=att, group_id=gid)
        await les.add(lesson); existing[(date_s, gid)] = lesson; changes += 1
        print(f"  + занятие {lid} {date_s} {gid} {dur}мин: {att.count(':') // 2} чел.")

    # ── групповые ──
    rows = src.worksheet("Яковлева О.").get_all_values()
    group_rosters = {gid: set(await sg.get_students_for_group(gid)) for gid, _, _ in GROUPS.values()}
    for titles, roster, marks in parse_blocks(rows, month):
        # Блок → группа бота: сначала по заголовку секции, иначе по максимальному
        # пересечению состава с составами групп (устойчиво к перестановке блоков).
        cfg = None
        for k, v in BLOCK_TITLES.items():
            if k in titles:
                cfg = GROUPS[v]
        if cfg is None:
            names = {_key(n) for n in roster}
            best = max(GROUPS.values(), key=lambda g: len(
                names & {_key(by_id[sid].name) for sid in group_rosters[g[0]] if sid in by_id}))
            cfg = best
        gid, dur, mode = cfg
        members = group_rosters[gid]
        trial_only = {n for n in roster if any(nn == n for d in marks for nn, _ in marks[d])
                      and all(v.upper() == "П" for d in marks for nn, v in marks[d] if nn == n)}
        # Состав = список из таблицы (Боброво: без пробных — иначе начислится абонемент)
        wanted = {(await ensure(n)).student_id for n in roster if mode == "per_visit" or n not in trial_only}
        for sid in sorted(wanted - members):
            await sg.add(sid, gid); changes += 1
            print(f"  → {by_id[sid].name} → {gid}")
        for sid in sorted(members - wanted):
            await sg.remove(sid, gid); changes += 1
            print(f"  ← {by_id.get(sid, sid) and by_id[sid].name} убран из {gid}")
        group_rosters[gid] = set(wanted)
        for day in sorted(marks):
            date_s = f"{month}-{day:02d}"
            l = existing.get((date_s, gid))
            prev = {e.split(":")[0]: e for e in (l.attendees or "").split(",") if e} if l else {}
            entries = []
            for name, v in marks[day]:
                s = await ensure(name)
                if mode == "per_visit":
                    if v.upper() == "П":
                        amt, mins = 0, dur
                    elif v in ("35", "60"):
                        amt, mins = PER_VISIT_SHORT if v == "35" else PER_VISIT_FULL
                    elif s.student_id in prev:
                        entries.append(prev[s.student_id]); continue  # «1»: тариф уже выбран в боте — сохраняем
                    else:
                        amt, mins = PER_VISIT_FULL
                else:
                    amt, mins = 0, dur
                entries.append(f"{s.student_id}:{mins}:{amt}")
            att = ",".join(entries)
            if l is None:
                await add_lesson(day, gid, dur, att)
            elif (l.attendees or "") != att:
                await les.update_attendees(l.lesson_id, att); l.attendees = att; changes += 1
                print(f"  ~ {l.lesson_id} {date_s} {gid}: состав приведён к таблице ({len(entries)} чел.)")

    # ── индивидуальные ──
    rows2 = src.worksheet("Индивидуальные занятия Яковлева ").get_all_values()
    for _titles, _roster, marks in parse_blocks(rows2, month):
        for day in sorted(marks):
            date_s = f"{month}-{day:02d}"
            for name, v in marks[day]:
                s = await ensure(name)
                n = int(v) if v.isdigit() else 1
                have = [l for (d, g), l in existing.items() if d == date_s and g == INDIVIDUAL_GROUP
                        and s.student_id in (l.attendees or "")]
                # одно занятие в день с несколькими девочками: добавляем участницу в существующее
                same_day = [l for (d, g), l in existing.items() if d == date_s and g == INDIVIDUAL_GROUP]
                if not have and same_day:
                    l = same_day[0]
                    await les.update_attendees(l.lesson_id, f"{l.attendees},{s.student_id}:60:{INDIVIDUAL_PRICE}")
                    l.attendees = f"{l.attendees},{s.student_id}:60:{INDIVIDUAL_PRICE}"; changes += 1
                    print(f"  ~ индивидуальное {l.lesson_id} {date_s}: +{name}")
                    have = [l]
                for _ in range(n - len(have)):
                    lid = generate_lesson_id(ids); ids.append(lid)
                    lesson = Lesson(lesson_id=lid, teacher_id=TEACHER[0], teacher_name=TEACHER[1], type=LessonType.GROUP,
                                    student_1_id=None, student_1_name=None, student_2_id=None, student_2_name=None,
                                    date=date_s, duration_min=60, earned=0, recorded_at=now_str(), updated_at=now_str(),
                                    attendees=f"{s.student_id}:60:{INDIVIDUAL_PRICE}", group_id=INDIVIDUAL_GROUP)
                    await les.add(lesson); existing[(date_s, INDIVIDUAL_GROUP)] = lesson; changes += 1
                    print(f"  + индивидуальное {lid} {date_s} {name}")
    print(f"готово: изменений {changes}")


if __name__ == "__main__":
    asyncio.run(main())
