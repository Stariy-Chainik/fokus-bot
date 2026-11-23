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
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram.types import Message

from bot.models import Student, User

logger = logging.getLogger(__name__)
_MOSCOW = ZoneInfo("Europe/Moscow")
DRAFT_DURATIONS = (45, 60, 90)
_ARCHIVE_RETENTION_DAYS = 30
_GREETING = re.compile(r"(?:доброе\s+утро|добрый\s+день|добрый\s+вечер|здравствуйте|всем\s+привет)[!.,]*")
_HEADER = re.compile(r"расписание\b(?: занятий)? со мной(?: (?:(?:на|в|во) )?(.+?))?[:.!]?")
_PLAIN_HEADER = re.compile(r"(?:мое )?расписание\b(?: занятий)?(?:(?: *: *| )(?:(?:на|в|во) )?(.+?))?[.!]?")
_WEEK = re.compile(r"(?:на )?(?:(эту|текущую|следующую) )?неделю")
_DAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_SHORT_DAYS = dict(zip(("пн", "вт", "ср", "чт", "пт", "сб", "вс"), _DAYS, strict=True))
_WEEKDAYS = {
    **{day: index for index, day in enumerate(_DAYS)},
    "среду": 2, "пятницу": 4, "субботу": 5,
    **{short: index for index, short in enumerate(_SHORT_DAYS)},
}
_DAY_HEADER = re.compile(rf"(?:{'|'.join(_DAYS)})(?: \d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{4}})?)?")
_INITIALS_TOKEN = re.compile(r"(?<![а-яa-z0-9])[сc]\.\s*[аa](?:\.|(?![а-яa-z0-9]))", re.IGNORECASE)
_HEADER_BULLET = re.compile(r"^(?:[♦🔶🔷🔸🔹◆▪▫•*—–\-📋🗓📅📌⚡]+\ufe0f?\s*)+")
_WEEK_DRAFT_STRIDE = 8  # семь отрицательных ключей на сообщение, по одному для каждого дня
_TIME = r"(?:[01]?\d|2[0-3])[:.][0-5]\d"
_SLOT_BULLET = r"[♦🔶🔷🔸🔹◆▪▫•*—–-]\ufe0f?"
_SLOT = re.compile(
    rf"(?:{_SLOT_BULLET}\s*)?(?P<start>{_TIME})"
    rf"(?:\s*[-–—]\s*(?P<end>{_TIME}))?(?:(?:\s*[-–—]\s*|\s+)(?P<names>.+))?",
)
_MINUTES = re.compile(r"(.+?)\s+(?:\((\d{1,3})\)|(\d{1,3})(?:\s*мин\.?)?)", re.IGNORECASE)
_GROUP = re.compile(r"\bгруппа\b")
_NAME_WORD = re.compile(r"[а-яa-z]+")
_NICKNAMES = {
    "екатерина": ("катя",), "николай": ("коля",), "софия": ("соня",), "софья": ("соня",),
    "ирина": ("ира",), "александр": ("саша",), "александра": ("саша",), "эмилия": ("эмили",),
}


def moscow_today() -> date:
    return datetime.now(_MOSCOW).date()


def is_draft_completed(plan: dict) -> bool:
    """True, если все рабочие занятия черновика уже внесены в журнал или отменены."""
    active = [slot for slot in plan["slots"] if slot["kind"] != "free"]
    if not active:
        return True
    if any(slot.get("status") not in ("held", "cancelled") for slot in active):
        return False
    if plan.get("sourceUpdated") and plan.get("telegram"):
        done_origins = {slot["telegramTime"] for slot in active}
        tg_active = [slot for slot in plan["telegram"]["slots"] if slot["kind"] != "free"]
        if any(slot["telegramTime"] not in done_origins for slot in tg_active):
            return False
    return True


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


def _clean_day_label(label: str) -> str:
    text = _plain(label)
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"\s*,\s*", " ", text)
    text = " ".join(text.split()).strip(" :.,!—–-")
    for prefix in ("на ", "во ", "в "):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text.rstrip(".!:")


def _plan_day(label: str, published: date) -> date | None:
    label = _clean_day_label(label)
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
        names, duration = explicit[1], int(explicit[2] or explicit[3])
        if duration <= 0:
            return None
    end = None
    if match["end"]:
        end, ends_at = _time_parts(match["end"])
        interval = ends_at - at
        if interval <= 0 or (duration is not None and duration != interval):
            return None
        duration = interval
    if not names.strip(" -–—") or _plain(names).rstrip(".!") == "свободно":
        names = ""
    return Slot(time, at, names, duration, end=end)


def _schedule_lines(text: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _GREETING.fullmatch(_plain(lines[0])):
        lines = lines[1:]
    if not lines:
        return "", []
    title = lines[0].rstrip(":").strip()
    greeting = _GREETING.match(title.casefold())
    if greeting and title[greeting.end():greeting.end() + 1].isspace():
        title = title[greeting.end():].strip()
    return title, lines[1:]


def _parse_slots(lines: list[str]) -> list[Slot] | None:
    slots: list[Slot] = []
    for line in lines:
        slot = _parse_slot(line)
        if slot is None or (slots and slot.at <= slots[-1].at):
            return None
        slots.append(slot)
    for i, slot in enumerate(slots[:-1]):
        gap = slots[i + 1].at - slot.at
        if slot.names and slot.duration is None and gap in DRAFT_DURATIONS:
            slots[i] = replace(slot, duration=gap, suggested=True)
    return slots or None


def _parse_owner_schedule(title: str, lines: list[str], published: date) -> tuple[date, list[Slot]] | None:
    has_initials = False
    day = None
    intro_and_slots = [title, *lines]
    for index, raw_line in enumerate(intro_and_slots):
        if _SLOT.fullmatch(raw_line):
            break
        cleaned = _HEADER_BULLET.sub("", raw_line).strip()
        if _INITIALS_TOKEN.search(cleaned):
            has_initials = True
            rest = _INITIALS_TOKEN.sub(" ", cleaned)
            rest = re.sub(r"\(\s*\)", "", rest).strip(" :.,!—–-")
            if rest and _SLOT.fullmatch(rest):
                intro_and_slots[index] = rest
                break
            plain_rest = _plain(rest).rstrip(":.!")
            header = _HEADER.fullmatch(plain_rest) or _PLAIN_HEADER.fullmatch(plain_rest)
            label = (header[1] or "") if header else plain_rest
        else:
            line = _plain(cleaned).rstrip(":.!")
            if _GREETING.fullmatch(line):
                continue
            header = _HEADER.fullmatch(line) or _PLAIN_HEADER.fullmatch(line)
            if header:
                label = header[1] or ""
            elif line and _plan_day(line, published) is not None:
                label = line
            else:
                break
        if label:
            candidate = _plan_day(label, published)
            if candidate is None or (day and day != candidate):
                return None
            day = candidate
    else:
        return None
    if not has_initials:
        return None
    slots = _parse_slots([line for line in intro_and_slots[index:] if not re.fullmatch(_SLOT_BULLET, line)])
    return (day or published, slots) if slots and any(slot.names for slot in slots) else None


def parse_schedule(
    text: str, published: date, *, accept_weekday: bool = True, accept_plain_title: bool = False,
    accept_owner_initials: bool = False,
) -> tuple[date, list[Slot]] | None:
    title, lines = _schedule_lines(text)
    if accept_owner_initials:
        owner_plan = _parse_owner_schedule(title, lines, published)
        if owner_plan is not None:
            return owner_plan
    title = _plain(title)
    header = _HEADER.fullmatch(title)
    plain_header = _PLAIN_HEADER.fullmatch(title) if accept_plain_title and header is None else None
    if header:
        day = _plan_day(header[1] or "", published)
    elif plain_header:
        label = plain_header[1] or ""
        if not label and lines:
            next_line = _plain(lines[0]).rstrip(":")
            if _DAY_HEADER.fullmatch(next_line) or next_line in ("сегодня", "завтра"):
                label = next_line
                lines = lines[1:]
        day = _plan_day(label, published)
    else:
        day = _plan_day(title, published) if accept_weekday and _DAY_HEADER.fullmatch(title) else None
    if day is None:
        return None
    slots = _parse_slots(lines)
    if not slots or (plain_header and not any(slot.names for slot in slots)):
        return None
    return day, slots


def _parse_week_schedule(text: str, published: date, *, accept_plain_title: bool) -> dict[date, list[Slot]]:
    title, lines = _schedule_lines(text)
    title = _plain(title)
    header = _HEADER.fullmatch(title) or (_PLAIN_HEADER.fullmatch(title) if accept_plain_title else None)
    if header is None:
        return {}
    label = header[1] or ""
    week = _WEEK.fullmatch(label)
    if label and week is None:
        return {}
    monday = published - timedelta(days=published.weekday())
    if week and week[1] == "следующую":
        monday += timedelta(days=7)
    sections: dict[date, list[str]] = {}
    day = None
    for line in lines:
        heading = _plain(line).rstrip(":")
        if _DAY_HEADER.fullmatch(heading):
            day = _plan_day(heading, monday)
            if day is None or day in sections or not 0 <= (day - monday).days < 7:
                return {}
            sections[day] = []
        elif day is None:
            return {}
        else:
            sections[day].append(line)
    if not sections or (week is None and len(sections) < 2):
        return {}
    plans: dict[date, list[Slot]] = {}
    for day, rows in sections.items():
        slots = _parse_slots(rows)
        if slots is None:
            return {}
        plans[day] = slots
    return plans


def _slot_kind(slot: dict) -> str:
    label = _plain(slot["names"]).rstrip(".!")
    inferred = "free" if label in ("", "свободно") else "group" if _GROUP.search(label) else "individual"
    return slot.get("kind", inferred)


def _telegram_plan(raw: str, source: str | None) -> dict:
    """Привязываем старые черновики к строкам поста, не считая свободное окно занятием."""
    plan = json.loads(raw)
    originals = {row["time"]: _slot_kind(row) for row in json.loads(source)["slots"]} if source else {}
    slots = []
    for row in plan["slots"]:
        original_time = row.get("telegram_time")
        if original_time is None and ("at" in row or (
            row["time"] in originals and (originals[row["time"]] == "free") == (_slot_kind(row) == "free")
        )):
            original_time = row["time"]
        if original_time is not None:
            slots.append({**row, "telegram_time": original_time})
    for row, next_row in pairwise(slots):
        # У старых импортированных слотов пересчитываем только неназначенную длительность.
        if "at" in row and "at" in next_row and row["duration"] is None and _slot_kind(row) != "free":
            gap = next_row["at"] - row["at"]
            if gap in DRAFT_DURATIONS:
                row.update(duration=gap, suggested=True)
    return {**plan, "slots": slots}


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
        kind = _slot_kind(slot)
        picked = slot.get("student_ids") or (self.match(text) if kind == "individual" else ())
        ids = [sid for sid in picked if sid in self.students]
        return {"time": slot["time"], "end": slot.get("end"), "text": text, "kind": kind, "duration": slot["duration"],
                "suggested": slot["suggested"], "groupId": slot.get("group_id"), "studentIds": ids,
                "students": [self.students[sid].name for sid in ids],
                "telegramTime": slot["telegram_time"]}


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
        origin = row.get("telegramTime")
        if not isinstance(origin, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", origin):
            raise ValueError("Добавляй новые занятия через обычный мастер")
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
                       "student_ids": ids, "group_id": group_id, "suggested": row.get("suggested") is True,
                       "telegram_time": origin}
    return {"date": day, "slots": [slots[time] for time in sorted(slots)]}


def _edit_telegram_slots(past: dict, new: dict, locked: set[str]) -> list[dict]:
    before = {s["telegram_time"]: s for s in past["slots"]}
    after = {s["telegram_time"]: s for s in new["slots"]}
    if before.keys() != after.keys() or len(after) != len(new["slots"]):
        raise ValueError("Черновик содержит только строки из Telegram. Новое занятие добавь через обычный мастер.")
    for origin, old in before.items():
        changed = after[origin]
        free = _slot_kind(old) == "free"
        if free != (_slot_kind(changed) == "free"):
            raise ValueError("Новое занятие на свободное время добавь через обычный мастер")
        if free or old["time"] in locked:
            if changed["time"] != old["time"]:
                raise ValueError("Свободные окна и уже отмеченные строки сохраняют своё время")
            after[origin] = old
        elif changed["duration"] != old["duration"] and changed["duration"] not in DRAFT_DURATIONS:
            raise ValueError("Выбери 45, 60 или 90 минут")
    return sorted(after.values(), key=lambda row: row["time"])


class DraftConflict(Exception):
    """Открытая в кабинете версия уже устарела."""


class SchedulePilot:
    def __init__(
        self, chat_id: int, tg_id: int, teacher_id: str, path: Path, *,
        accept_weekday: bool = True, accept_plain_title: bool = False, accept_weekly: bool = False,
        accept_owner_initials: bool = False,
    ):
        self.chat_id = chat_id
        self.tg_id = tg_id
        self.teacher_id = teacher_id
        self.path = path
        self.accept_weekday = accept_weekday
        self.accept_plain_title = accept_plain_title
        self.accept_weekly = accept_weekly
        self.accept_owner_initials = accept_owner_initials
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
        text = message.text or ""
        parsed = parse_schedule(
            text, published.date(), accept_weekday=self.accept_weekday, accept_plain_title=self.accept_plain_title,
            accept_owner_initials=self.accept_owner_initials,
        )
        plans: dict[int, tuple[date, list[Slot]]] = {}
        if parsed is not None:
            plans[message.message_id] = parsed
        elif self.accept_weekly:
            for day, slots in _parse_week_schedule(text, published.date(), accept_plain_title=self.accept_plain_title).items():
                draft_id = -(message.message_id * _WEEK_DRAFT_STRIDE + day.weekday())
                plans[draft_id] = (day, slots)
        if not plans and not edited:
            return
        payloads = {
            draft_id: json.dumps({"date": day.isoformat(), "published": published.isoformat(),
                                  "slots": [asdict(slot) for slot in slots]}, ensure_ascii=False)
            for draft_id, (day, slots) in plans.items()
        }
        modified = message.edit_date
        version = int(modified.timestamp()) if isinstance(modified, datetime) else modified or int(message.date.timestamp())
        stored = await asyncio.to_thread(self._save, message.message_id, version, update_id, payloads)
        if stored and plans:
            logger.info("Черновик расписания получен: чат %s, пост %s, педагог %s",
                        self.chat_id, message.message_id, self.teacher_id)

    _INITIALIZED_DBS: set[Path] = set()

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = self.path.resolve()
        existed = resolved.exists()
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.execute("PRAGMA busy_timeout = 15000")
        if resolved not in self._INITIALIZED_DBS or not existed:
            conn.execute("PRAGMA journal_mode = WAL")
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
            conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                sender_name TEXT NOT NULL,
                sent_at INTEGER NOT NULL,
                edited_at INTEGER,
                text TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_chat_messages_sent
                ON chat_messages (chat_id, sent_at DESC)""")
            conn.commit()
            self._INITIALIZED_DBS.add(resolved)
        return conn

    def _save_chat_message(
        self, message_id: int, sender_id: int, sender_name: str, sent_at: int, edited_at: int | None, text: str,
    ) -> None:
        cutoff = sent_at - _ARCHIVE_RETENTION_DAYS * 86400
        with closing(self._open()) as conn, conn:
            conn.execute("""INSERT INTO chat_messages VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    sender_name=excluded.sender_name,
                    edited_at=excluded.edited_at,
                    text=excluded.text
                WHERE COALESCE(excluded.edited_at, excluded.sent_at) >= COALESCE(chat_messages.edited_at, chat_messages.sent_at)
            """, (self.chat_id, message_id, sender_id, sender_name, sent_at, edited_at, text))
            conn.execute("DELETE FROM chat_messages WHERE chat_id=? AND sent_at < ?", (self.chat_id, cutoff))

    async def archive_message(self, message: Message) -> None:
        """Сохранить текст/подпись сообщения из рабочей группы расписаний (срок хранения 30 дней)."""
        sender = message.from_user
        if message.chat.id != self.chat_id or sender is None or sender.is_bot:
            return
        text = (message.text or message.caption or "").strip()
        if not text:
            return
        sender_name = " ".join(p for p in (sender.first_name, sender.last_name) if p).strip() or sender.username or str(sender.id)
        sent_at = int(message.date.timestamp())
        modified = message.edit_date
        edited_at = int(modified.timestamp()) if isinstance(modified, datetime) else (int(modified) if modified else None)
        await asyncio.to_thread(self._save_chat_message, message.message_id, sender.id, sender_name, sent_at, edited_at, text)

    def _read_recent_messages(self, limit: int) -> list[dict]:
        with closing(self._open()) as conn:
            rows = conn.execute("""SELECT message_id, sender_id, sender_name, sent_at, edited_at, text
                FROM chat_messages WHERE chat_id=? ORDER BY sent_at DESC, message_id DESC LIMIT ?""",
                (self.chat_id, limit)).fetchall()
        return [
            {"messageId": mid, "senderId": sid, "senderName": name,
             "sentAt": datetime.fromtimestamp(sent, _MOSCOW).isoformat(),
             "editedAt": datetime.fromtimestamp(edited, _MOSCOW).isoformat() if edited else None,
             "text": text}
            for mid, sid, name, sent, edited, text in rows
        ]

    async def recent_messages(self, limit: int = 30) -> list[dict]:
        return await asyncio.to_thread(self._read_recent_messages, limit)

    def _save(self, message_id: int, version: int, update_id: int, payloads: dict[int, str]) -> bool:
        account = (self.chat_id, self.tg_id, self.teacher_id)
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("""SELECT version, update_id FROM drafts
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""",
                (*account, message_id)).fetchone()
            if current is not None and current >= (version, update_id):
                return False
            week_key = -message_id * _WEEK_DRAFT_STRIDE
            older_days = conn.execute("""SELECT message_id FROM drafts
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id BETWEEN ? AND ?
                    AND payload IS NOT NULL""", (*account, week_key - 6, week_key)).fetchall()
            draft_ids = {message_id, *payloads, *(draft_id for (draft_id,) in older_days)}
            for draft_id in draft_ids:
                self._save_draft(conn, draft_id, version, update_id, payloads.get(draft_id))
            return True

    def _save_draft(self, conn: sqlite3.Connection, draft_id: int, version: int, update_id: int, payload: str | None) -> None:
        key = (self.chat_id, self.tg_id, self.teacher_id, draft_id)
        previous = conn.execute("""SELECT d.payload, e.payload FROM drafts AS d JOIN draft_edits AS e
                USING (chat_id, sender_id, teacher_id, message_id)
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""", key).fetchone()
        if previous and any("telegram_time" not in s for s in json.loads(previous[1])["slots"]):
            normalized = _telegram_plan(previous[1], previous[0])
            conn.execute("""UPDATE draft_edits SET payload=?
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""",
                (json.dumps(normalized, ensure_ascii=False), *key))
        conn.execute("""INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET version=excluded.version, update_id=excluded.update_id,
            payload=excluded.payload
        """, (*key, version, update_id, payload))

    def _latest(self) -> list[tuple]:
        with closing(self._open()) as conn:
            return list(conn.execute("""SELECT message_id, d.version, d.update_id, d.payload,
                    e.revision, e.source_revision, e.payload
                FROM drafts AS d LEFT JOIN draft_edits AS e USING (chat_id, sender_id, teacher_id, message_id)
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND (d.payload IS NOT NULL OR e.payload IS NOT NULL)
                ORDER BY CASE WHEN message_id < 0 THEN -message_id / ? ELSE message_id END DESC, message_id DESC
                LIMIT 10""", (self.chat_id, self.tg_id, self.teacher_id, _WEEK_DRAFT_STRIDE)))

    @staticmethod
    def _source_revision(version: int, update_id: int) -> str:
        return f"{version}:{update_id}"

    def _write_edit(self, message_id: int, revision: int, base: str, payload: str | None) -> None:
        key = (self.chat_id, self.tg_id, self.teacher_id, message_id)
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute("""SELECT version, update_id, payload FROM drafts
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""", key).fetchone()
            edit = conn.execute("""SELECT revision, payload, source_revision FROM draft_edits
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id=?""", key).fetchone()
            if source is None or (source[2] is None and edit is None):
                raise KeyError(message_id)
            if (edit[0] if edit else 0) != revision:
                raise DraftConflict
            latest = self._source_revision(source[0], source[1])
            previous = _telegram_plan(edit[1] if edit else source[2], source[2])
            locked_times = set(self._outcomes(conn, message_id))
            if payload is None:
                if source[2] is None or latest != base:
                    raise DraftConflict
                new = _telegram_plan(source[2], source[2])
                locked = [row for row in previous["slots"] if row["time"] in locked_times]
                locked_origins = {row["telegram_time"] for row in locked}
                remaining = [row for row in new["slots"] if row["time"] not in locked_times
                             and row["telegram_time"] not in locked_origins]
                new["slots"] = sorted(remaining + locked, key=lambda row: row["time"])
            else:
                if base != (edit[2] if edit else latest):
                    raise DraftConflict
                new = json.loads(payload)
                new["slots"] = _edit_telegram_slots(previous, new, locked_times)
            if locked_times and previous["date"] != new["date"]:
                raise ValueError("Дата закреплена после отметки занятий")
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

    def _latest_with_outcomes(self) -> tuple[list[tuple], dict[int, dict[str, tuple[str, str, str | None]]]]:
        """Одним подключением читаем последние 20 черновиков и все их результаты (без N+1)."""
        with closing(self._open()) as conn:
            rows = list(conn.execute("""SELECT message_id, d.version, d.update_id, d.payload,
                    e.revision, e.source_revision, e.payload
                FROM drafts AS d LEFT JOIN draft_edits AS e USING (chat_id, sender_id, teacher_id, message_id)
                WHERE chat_id=? AND sender_id=? AND teacher_id=? AND (d.payload IS NOT NULL OR e.payload IS NOT NULL)
                ORDER BY CASE WHEN message_id < 0 THEN -message_id / ? ELSE message_id END DESC, message_id DESC
                LIMIT 20""", (self.chat_id, self.tg_id, self.teacher_id, _WEEK_DRAFT_STRIDE)))
            outcomes_by_mid: dict[int, dict[str, tuple[str, str, str | None]]] = {}
            if rows:
                mids = [r[0] for r in rows]
                placeholders = ",".join("?" for _ in mids)
                for mid, slot_time, status, lesson, body in conn.execute(
                    f"""SELECT message_id, time, status, lesson_id, request FROM draft_results
                        WHERE chat_id=? AND sender_id=? AND teacher_id=? AND message_id IN ({placeholders})""",
                    (self.chat_id, self.tg_id, self.teacher_id, *mids),
                ):
                    outcomes_by_mid.setdefault(mid, {})[slot_time] = (status, lesson, body)
            return rows, outcomes_by_mid

    async def today_summary(self, day: date) -> tuple[int, bool]:
        """Возвращает (сколько незаписанных занятий на день, есть ли полностью внесённый черновик на этот день)."""
        day_iso = day.isoformat()
        rows, outcomes_by_mid = await asyncio.to_thread(self._latest_with_outcomes)
        pending = 0
        completed = False
        empty_names = StudentNames([])
        for message_id, version, update_id, source, _revision, base, edit in rows:
            latest = self._source_revision(version, update_id)
            plan = self._view(_telegram_plan(edit or source, source), empty_names)
            if plan["date"] != day_iso:
                continue
            plan["sourceUpdated"] = bool(edit and base != latest)
            outcomes = outcomes_by_mid.get(message_id, {})
            for slot in plan["slots"]:
                status, _lesson_id, _ = outcomes.get(slot["time"], ("planned", "", None))
                slot["status"] = status
            if plan["sourceUpdated"] and source:
                plan["telegram"] = self._view(_telegram_plan(source, source), empty_names)
            if is_draft_completed(plan):
                completed = True
            else:
                pending += sum(
                    1 for slot in plan["slots"]
                    if slot["kind"] != "free" and slot["status"] not in ("held", "cancelled")
                )
        return pending, completed

    async def drafts(self, students: list[Student]) -> list[dict]:
        names = StudentNames(students)
        chat_slug = str(self.chat_id).removeprefix("-100")
        rows, outcomes_by_mid = await asyncio.to_thread(self._latest_with_outcomes)
        result = []
        for message_id, version, update_id, source, revision, base, edit in rows:
            source_id = -message_id // _WEEK_DRAFT_STRIDE if message_id < 0 else message_id
            latest = self._source_revision(version, update_id)
            plan = self._view(_telegram_plan(edit or source, source), names)
            plan.update(id=message_id, revision=revision or 0, sourceRevision=base or latest,
                        source=f"https://t.me/c/{chat_slug}/{source_id}", sourceUpdated=bool(edit and base != latest))
            outcomes = outcomes_by_mid.get(message_id, {})
            for slot in plan["slots"]:
                status, lesson_id, _ = outcomes.get(slot["time"], ("planned", "", None))
                slot.update(status=status, lessonId=lesson_id if status == "held" else "")
            if plan["sourceUpdated"] and source:
                plan["telegram"] = dict(self._view(_telegram_plan(source, source), names), sourceRevision=latest)
            result.append(plan)
        return result

    @staticmethod
    def _view(plan: dict, names: StudentNames) -> dict:
        return {"date": plan["date"], "slots": [names.row(slot) for slot in plan["slots"]]}


class SchedulePilots:
    """Изолированные черновики из одного источника на аккаунт педагога."""

    def __init__(self, accounts: list[SchedulePilot]):
        self.accounts = {pilot.tg_id: pilot for pilot in accounts}
        if len(self.accounts) != len(accounts):
            raise ValueError("У Telegram-аккаунта может быть один источник расписания")
        self._archive_pilot = accounts[0] if accounts else None

    def for_user(self, user: User) -> SchedulePilot | None:
        pilot = self.accounts.get(user.tg_id)
        return pilot if pilot and pilot.owns(user) else None

    async def recent_messages(self, limit: int = 30) -> list[dict]:
        return await self._archive_pilot.recent_messages(limit) if self._archive_pilot else []

    async def receive(self, message: Message, update_id: int, *, edited: bool) -> None:
        if self._archive_pilot and message.chat.id == self._archive_pilot.chat_id:
            try:
                await self._archive_pilot.archive_message(message)
            except Exception:
                logger.warning("Не удалось сохранить сообщение %s в архив чата", message.message_id, exc_info=True)
        if message.from_user:
            pilot = self.accounts.get(message.from_user.id)
            if pilot:
                await pilot.receive(message, update_id, edited=edited)
