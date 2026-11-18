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
        kind = "free" if label in ("", "свободно") else "group" if label == "группа" else "individual"
        ids = self.match(text) if kind == "individual" else ()
        return {"time": slot["time"], "end": slot.get("end"), "text": text, "kind": kind, "duration": slot["duration"],
                "suggested": slot["suggested"], "students": [self.students[sid].name for sid in ids]}


class SchedulePilot:
    def __init__(self, chat_id: int, tg_id: int, teacher_id: str, path: Path):
        self.chat_id = chat_id
        self.tg_id = tg_id
        self.teacher_id = teacher_id
        self.path = path

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
        return conn

    def _save(self, message_id: int, version: int, update_id: int, payload: str | None) -> bool:
        with closing(self._open()) as conn, conn:
            cursor = conn.execute("""INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET version=excluded.version, update_id=excluded.update_id,
                payload=excluded.payload WHERE (excluded.version, excluded.update_id) > (drafts.version, drafts.update_id)
            """, (self.chat_id, self.tg_id, self.teacher_id, message_id, version, update_id, payload))
            return cursor.rowcount > 0

    def _latest(self) -> list[tuple[int, str]]:
        with closing(self._open()) as conn:
            return list(conn.execute("""SELECT message_id, payload FROM drafts WHERE chat_id=? AND sender_id=?
                AND teacher_id=? AND payload IS NOT NULL ORDER BY message_id DESC LIMIT 10""",
                (self.chat_id, self.tg_id, self.teacher_id)))

    async def drafts(self, students: list[Student]) -> list[dict]:
        names = StudentNames(students)
        chat_slug = str(self.chat_id).removeprefix("-100")
        result = []
        for message_id, raw in await asyncio.to_thread(self._latest):
            plan = json.loads(raw)
            plan["slots"] = [names.row(slot) for slot in plan["slots"]]
            plan["source"] = f"https://t.me/c/{chat_slug}/{message_id}"
            result.append(plan)
        return result
