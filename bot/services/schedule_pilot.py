"""Черновики расписания из одного Telegram-чата для одного педагога."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram.types import Message

from bot.models import Student, User

logger = logging.getLogger(__name__)
_MOSCOW = ZoneInfo("Europe/Moscow")
_GREETING = re.compile(r"(?:доброе утро|добрый день|добрый вечер|здравствуйте|всем привет)[!.,]*")
_HEADER = re.compile(r"расписание со мной(?: (?:на )?(.+?))?[:.!]?")
_DAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_WEEKDAYS = {**{day: index for index, day in enumerate(_DAYS)}, "среду": 2, "пятницу": 4, "субботу": 5}
_DAY_HEADER = re.compile(rf"(?:{'|'.join(_DAYS)})(?: \d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{4}})?)?")
_TIME = r"(?:[01]?\d|2[0-3])[:.][0-5]\d"
_SLOT = re.compile(
    rf"(?:[♦🔶🔷🔸🔹◆▪•-]\ufe0f?\s*)?(?P<start>{_TIME})"
    rf"(?:\s*[-–—]\s*(?P<end>{_TIME}))?(?:(?:\s*[-–—]\s*|\s+)(?P<names>.+))?",
)
_MINUTES = re.compile(r"(.+?)\s+(\d{1,3})(?:\s*мин\.?)?", re.IGNORECASE)
_NAME_WORD = re.compile(r"[а-яa-z]+")
_NICKNAMES = {
    "екатерина": ("катя",), "николай": ("коля",), "софия": ("соня",), "софья": ("соня",),
    "ирина": ("ира",), "александр": ("саша",), "александра": ("саша",), "эмилия": ("эмили",),
}


def _plain(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class Slot:
    time: str
    at: int
    names: str
    duration: int | None
    suggested: bool = False
    end: str | None = None


def _plan_day(label: str, published: date) -> date | None:
    if label in ("", "сегодня"):
        return published
    if label == "завтра":
        return published + timedelta(days=1)
    if label in _WEEKDAYS:
        return published + timedelta(days=(_WEEKDAYS[label] - published.weekday()) % 7)
    explicit = re.fullmatch(r"(?:(\w+) )?(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", label)
    if explicit:
        weekday, day, month, year = explicit.groups()
        try:
            target = date(int(year) if year else published.year, int(month), int(day))
            if year is None and (published - target).days > 180:
                target = target.replace(year=target.year + 1)
        except ValueError:
            return None
        if weekday is None or _WEEKDAYS.get(weekday) == target.weekday():
            return target
    return None


def _time_parts(value: str) -> tuple[str, int]:
    hour, minute = map(int, value.replace(".", ":").split(":"))
    return f"{hour:02d}:{minute:02d}", hour * 60 + minute


def _parse_slot(line: str) -> Slot | None:
    match = _SLOT.fullmatch(line)
    if not match:
        return None
    time, at = _time_parts(match["start"])
    names = (match["names"] or "").strip()
    if re.match(r"\d{1,2}[:.]\d", names):
        return None  # некорректное время окончания не считаем именем
    explicit = _MINUTES.fullmatch(names)
    duration = None
    if explicit:
        names, duration = explicit[1], int(explicit[2])
        if duration <= 0:
            return None
    end = None
    if match["end"]:
        end, ends_at = _time_parts(match["end"])
        interval = ends_at - at
        if interval <= 0 or (duration is not None and duration != interval):
            return None
        duration = interval
    if _plain(names).rstrip(".!") == "свободно":
        names = ""
    return Slot(time, at, names, duration, end=end)


def parse_schedule(text: str, published: date) -> tuple[date, list[Slot]] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _GREETING.fullmatch(_plain(lines[0])):
        lines = lines[1:]
    if not lines:
        return None
    title = _plain(lines[0]).rstrip(":")
    header = _HEADER.fullmatch(title)
    if header:
        day = _plan_day(header[1] or "", published)
    else:
        day = _plan_day(title, published) if _DAY_HEADER.fullmatch(title) else None
    if day is None:
        return None
    slots: list[Slot] = []
    for line in lines[1:]:
        slot = _parse_slot(line)
        if slot is None:
            return None  # в том числе второй заголовок с расписанием другого педагога
        if slots and slot.at <= slots[-1].at:
            return None
        slots.append(slot)
    if not slots:
        return None
    for i, slot in enumerate(slots[:-1]):
        if slot.names and slot.duration is None and slots[i + 1].at - slot.at == 45:
            slots[i] = replace(slot, duration=45, suggested=True)
    return day, slots


class StudentNames:
    def __init__(self, students: list[Student]):
        self.students = {s.student_id: s for s in students if not s.is_archived}
        self.aliases: dict[tuple[str, ...], set[str]] = {}
        for sid, student in self.students.items():
            words = self.words(student.name)
            if not words:
                continue
            variants = {words, (words[0],)}
            if len(words) >= 2:
                surname, given = words[:2]
                variants.update({(surname, given), (given, surname)})
                for name in (given, *_NICKNAMES.get(given, ())):
                    variants.update({(name,), (name, surname[0])})
            for variant in variants:
                self.aliases.setdefault(variant, set()).add(sid)

    @staticmethod
    def words(text: str) -> tuple[str, ...]:
        return tuple(_NAME_WORD.findall(_plain(text)))

    def match(self, text: str) -> tuple[str, ...]:
        words = self.words(text)
        solo = {(sid,) for sid in self.aliases.get(words, ())}
        pairs: set[tuple[str, ...]] = set()
        if "и" in words:
            splits = [(words[:pos], words[pos + 1:]) for pos, word in enumerate(words) if word == "и"]
        else:
            splits = [(words[:pos], words[pos:]) for pos in range(1, len(words))]
        for left_words, right_words in splits:
            right = self.aliases.get(right_words, ())
            for left_id in self.aliases.get(left_words, ()):
                partner = self.students[left_id].partner_id
                if partner and partner != left_id and partner in right and self.students[partner].partner_id == left_id:
                    pairs.add(tuple(sorted((left_id, partner))))
        choices = solo | pairs
        return next(iter(choices)) if len(choices) == 1 else ()

    def row(self, slot: dict) -> dict:
        text = slot["names"]
        label = _plain(text).rstrip(".!")
        inferred = "free" if label in ("", "свободно") else "group" if label == "группа" else "individual"
        kind = slot.get("kind", inferred)
        ids = slot.get("student_ids") or (self.match(text) if kind == "individual" else ())
        return {"time": slot["time"], "end": slot.get("end"), "text": text, "kind": kind, "duration": slot["duration"],
                "suggested": slot["suggested"], "groupId": slot.get("group_id"), "studentIds": list(ids),
                "students": [self.students[sid].name for sid in ids if sid in self.students]}


def edited_schedule(body: dict, students: list[Student], groups: dict[str, str]) -> dict:
    """Проверяем поля черновика и сохраняем выбранные карточки вместе с их названиями."""
    day, rows = body.get("date"), body.get("slots")
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Укажи дату расписания")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("Проверь дату расписания") from None
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Проверь список занятий")
    student_names = {s.student_id: s.name for s in students}
    slots: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Проверь список занятий")
        time, kind, duration = row.get("time"), row.get("kind"), row.get("duration")
        if not isinstance(time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            raise ValueError("Укажи время начала каждой строки")
        if time in slots:
            raise ValueError(f"Две строки начинаются в {time}. Проверь время")
        if kind not in ("individual", "group", "free"):
            raise ValueError("Выбери тип занятия")
        if duration is not None and (type(duration) is not int or not 1 <= duration <= 1440):
            raise ValueError(f"Проверь длительность строки {time}")
        ids = row.get("studentIds") or []
        group_id = row.get("groupId") or None
        text = row.get("text") or ""
        if not isinstance(text, str) or len(text) > 300:
            raise ValueError(f"Слишком длинная подпись в строке {time}")
        if kind == "individual":
            if (not isinstance(ids, list) or len(ids) > 2
                    or any(not isinstance(sid, str) or sid not in student_names for sid in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError(f"Проверь состав в строке {time}")
            text = " + ".join(student_names[sid] for sid in ids) if ids else text.strip()
            if not text:
                raise ValueError(f"Укажи участника в строке {time}")
        else:
            ids = []
        if kind == "group":
            if group_id is not None and (not isinstance(group_id, str) or group_id not in groups):
                raise ValueError(f"Проверь группу в строке {time}")
            text = groups[group_id] if group_id else "группа"
        else:
            group_id = None
        if kind == "free":
            text = ""
        _, start = _time_parts(time)
        finish = start + (duration or 0)
        end = f"{finish // 60:02d}:{finish % 60:02d}" if duration and finish < 1440 else None
        slots[time] = {"time": time, "end": end, "duration": duration, "kind": kind, "names": text,
                       "student_ids": ids, "group_id": group_id, "suggested": row.get("suggested") is True}
    return {"date": day, "slots": [slots[time] for time in sorted(slots)]}


class DraftConflict(Exception):
    """Открытая в кабинете версия уже устарела."""


class SchedulePilot:
    def __init__(self, chat_id: int, tg_id: int, teacher_id: str, path: Path):
        self.chat_id = chat_id
        self.tg_id = tg_id
        self.teacher_id = teacher_id
        self.path = path
        self.lock = asyncio.Lock()

    def owns(self, user: User) -> bool:
        return user.tg_id == self.tg_id and user.teacher_id == self.teacher_id

    async def receive(self, message: Message, update_id: int, *, edited: bool) -> None:
        """Проверяем реального отправителя и заголовок, даже если пост создан владельцем."""
        sender = message.from_user
        if (message.chat.id != self.chat_id or sender is None or sender.id != self.tg_id
                or sender.is_bot or message.sender_chat is not None or message.forward_origin is not None):
            return
        published = message.date.astimezone(_MOSCOW)
        parsed = parse_schedule(message.text or "", published.date())
        if parsed is None and not edited:
            return
        payload = None
        if parsed:
            day, slots = parsed
            payload = json.dumps({"date": day.isoformat(), "published": published.isoformat(),
                                  "slots": [asdict(slot) for slot in slots]}, ensure_ascii=False)
        modified = message.edit_date
        version = int(modified.timestamp()) if isinstance(modified, datetime) else modified or int(message.date.timestamp())
        stored = await asyncio.to_thread(self._save, message.message_id, version, update_id, payload)
        if stored and payload:
            logger.info("Черновик расписания получен: чат %s, пост %s, педагог %s",
                        self.chat_id, message.message_id, self.teacher_id)

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
            chat_id INTEGER, sender_id INTEGER, teacher_id TEXT, message_id INTEGER,
            version INTEGER, update_id INTEGER, payload TEXT,
            PRIMARY KEY (chat_id, sender_id, teacher_id, message_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS draft_edits (
            chat_id INTEGER, sender_id INTEGER, teacher_id TEXT, message_id INTEGER,
            revision INTEGER NOT NULL, source_revision TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (chat_id, sender_id, teacher_id, message_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS draft_results (
            chat_id INTEGER, sender_id INTEGER, teacher_id TEXT, message_id INTEGER, time TEXT,
            status TEXT NOT NULL, lesson_id TEXT NOT NULL, request TEXT,
            PRIMARY KEY (chat_id, sender_id, teacher_id, message_id, time)
        )""")
        return conn

    def _save(self, message_id: int, version: int, update_id: int, payload: str | None) -> bool:
        with closing(self._open()) as conn, conn:
            cursor = conn.execute("""INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET version=excluded.version, update_id=excluded.update_id,
                payload=excluded.payload WHERE (excluded.version, excluded.update_id) > (drafts.version, drafts.update_id)
            """, (self.chat_id, self.tg_id, self.teacher_id, message_id, version, update_id, payload))
            return cursor.rowcount > 0

    def _latest(self) -> list[tuple]:
        with closing(self._open()) as conn:
            return list(conn.execute("""SELECT message_id, d.version, d.update_id, d.payload,
                    e.revision, e.source_revision, e.payload
                FROM drafts AS d LEFT JOIN draft_edits AS e USING (chat_id, sender_id, teacher_id, message_id)
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND (d.payload IS NOT NULL OR e.payload IS NOT NULL)
                ORDER BY message_id DESC LIMIT 10""",
                (self.chat_id, self.tg_id, self.teacher_id)))

    @staticmethod
    def _source_revision(version: int, update_id: int) -> str:
        return f"{version}:{update_id}"

    def _write_edit(self, message_id: int, revision: int, base: str, payload: str | None) -> None:
        key = (self.chat_id, self.tg_id, self.teacher_id, message_id)
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute("""SELECT version, update_id, payload FROM drafts
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""", key).fetchone()
            edit = conn.execute("""SELECT revision, payload FROM draft_edits
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""", key).fetchone()
            if source is None or (source[2] is None and edit is None):
                raise KeyError(message_id)
            if (edit[0] if edit else 0) != revision:
                raise DraftConflict
            if payload is None:  # явное применение увиденной пользователем версии из Telegram
                if source[2] is None or self._source_revision(source[0], source[1]) != base:
                    raise DraftConflict
                payload = source[2]
            locked_times = set(self._outcomes(conn, message_id))
            if locked_times:
                previous = json.loads(edit[1] if edit else source[2])
                new = json.loads(payload)
                if previous["date"] != new["date"]:
                    raise ValueError("Дата закреплена после отметки занятий")
                rows = {row["time"]: row for row in new["slots"]}
                rows.update((row["time"], row) for row in previous["slots"] if row["time"] in locked_times)
                new["slots"] = [rows[time] for time in sorted(rows)]
                payload = json.dumps(new, ensure_ascii=False)
            conn.execute("""INSERT INTO draft_edits VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET revision=excluded.revision, source_revision=excluded.source_revision,
                    payload=excluded.payload""", (*key, revision + 1, base, payload))

    async def save_edit(self, message_id: int, revision: int, source_revision: str, plan: dict | None) -> None:
        raw = json.dumps(plan, ensure_ascii=False) if plan is not None else None
        await asyncio.to_thread(self._write_edit, message_id, revision, source_revision, raw)

    def _outcomes(self, conn: sqlite3.Connection, message_id: int) -> dict[str, tuple[str, str, str | None]]:
        return {time: (status, lesson, body) for time, status, lesson, body in conn.execute("""
            SELECT time, status, lesson_id, request FROM draft_results
            WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?
        """, (self.chat_id, self.tg_id, self.teacher_id, message_id))}

    def _read_outcomes(self, message_id: int) -> dict[str, tuple[str, str, str | None]]:
        with closing(self._open()) as conn:
            return self._outcomes(conn, message_id)

    async def outcomes(self, message_id: int) -> dict[str, tuple[str, str, str | None]]:
        return await asyncio.to_thread(self._read_outcomes, message_id)

    def _change_outcome(self, message_id: int, time: str, status: str, lesson_id: str, body: str | None) -> None:
        key = (self.chat_id, self.tg_id, self.teacher_id, message_id, time)
        with closing(self._open()) as conn, conn:
            if status == "planned":
                conn.execute("""DELETE FROM draft_results
                    WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=? AND time=?""", key)
            else:
                conn.execute("""INSERT INTO draft_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO UPDATE SET status=excluded.status, lesson_id=excluded.lesson_id, request=excluded.request
                """, (*key, status, lesson_id, body))

    async def set_outcome(self, message_id: int, time: str, status: str, lesson_id: str = "", body: dict | None = None) -> None:
        raw = json.dumps(body) if body is not None else None
        await asyncio.to_thread(self._change_outcome, message_id, time, status, lesson_id, raw)

    async def drafts(self, students: list[Student]) -> list[dict]:
        names = StudentNames(students)
        chat_slug = str(self.chat_id).removeprefix("-100")
        result = []
        for message_id, version, update_id, source, revision, base, edit in await asyncio.to_thread(self._latest):
            latest = self._source_revision(version, update_id)
            plan = self._view(edit or source, names)
            plan.update(id=message_id, revision=revision or 0, sourceRevision=base or latest,
                        source=f"https://t.me/c/{chat_slug}/{message_id}", sourceUpdated=bool(edit and base != latest))
            outcomes = await self.outcomes(message_id)
            for slot in plan["slots"]:
                status, lesson_id, _ = outcomes.get(slot["time"], ("planned", "", None))
                slot.update(status=status, lessonId=lesson_id if status == "held" else "")
            if plan["sourceUpdated"] and source:
                plan["telegram"] = dict(self._view(source, names), sourceRevision=latest)
            result.append(plan)
        return result

    @staticmethod
    def _view(raw: str, names: StudentNames) -> dict:
        plan = json.loads(raw)
        return {"date": plan["date"], "slots": [names.row(slot) for slot in plan["slots"]]}
