"""Движение учеников за месяц — плитки «пришли / ушли» на сводке администратора (решение владельца 16.10.2026).

Считается по составам групп (`student_groups.joined_period` / `left_period`) и ленте событий за месяц (удаление
карточки, «убран из группы» у групп по посещению — у них строка членства удаляется, новая карточка без группы).
Три списка, в каждом ученик один раз:

- **new** («пришли») — впервые в школе: первое членство началось в этом месяце (раньше ни в одной группе не был)
  или карточка заведена в этом месяце без группы;
- **gone** («ушли») — ушли из школы: членство закончилось в этом месяце (`left_period` = месяц или «убран из
  группы» по посещению) и других групп не осталось;
- **moves** — внутри школы: перешёл из группы в группу, добавлен ещё в одну, ушёл из одной группы, оставшись в другой —
  считаются, чтобы не попасть в new/gone, но **наружу не отдаются** (решение владельца 16.10.2026: переводы на сводке
  не показывать, достаточно ленты событий);
- **deleted** — удалённые в этом месяце карточки: отдельным списком и **в «ушли» не считаются** — на проде это в
  основном чистка дублей (16.10.2026: 17 удалений за октябрь без единого имени); ушедшего ученика отмечают
  «ушёл из группы», и он попадает в gone.

Плитки на сводке — только new/gone (переводы и удаления школу не меняют). Чистая функция `month_flow`, без таблицы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.utils.dates import month_name_ru

# «с октября», «с ноября» — родительный падеж месяца
_GENITIVE = {"январь": "января", "февраль": "февраля", "март": "марта", "апрель": "апреля", "май": "мая",
             "июнь": "июня", "июль": "июля", "август": "августа", "сентябрь": "сентября", "октябрь": "октября",
             "ноябрь": "ноября", "декабрь": "декабря"}

_REMOVED = re.compile(r"Ученик (STU-\d{4}) убран из группы (GRP-\d{4})")
_TRANSFER = re.compile(r"Ученик (STU-\d{4}) переведён .*?: (GRP-\d{4}) → (GRP-\d{4})")
_DELETED = re.compile(r"Удалён ученик (STU-\d{4})(?: (.+))?$")
_CREATED = re.compile(r"Новый ученик(?: по заявке родителя)?: (STU-\d{4})")
_ADDED = re.compile(r"Ученик (STU-\d{4}) добавлен в группу (GRP-\d{4})")
_LEFT = re.compile(r"Ученик (STU-\d{4}) уходит из группы (GRP-\d{4})")


@dataclass
class FlowItem:
    student_id: str
    name: str
    groups: list = field(default_factory=list)      # названия групп, о которых речь (куда пришёл / откуда ушёл)
    note: str = ""                                   # «с ноября», «карточка удалена», «из ВБ ХГ Младшая»…
    date: str = ""                                   # день события из ленты (YYYY-MM-DD); пусто — известен только месяц

    def as_dict(self) -> dict:
        return {"id": self.student_id, "name": self.name, "groups": self.groups, "note": self.note, "date": self.date}


@dataclass
class FlowReport:
    period: str
    new: list = field(default_factory=list)
    gone: list = field(default_factory=list)
    moves: list = field(default_factory=list)
    deleted: list = field(default_factory=list)

    @property
    def net(self) -> int:
        return len(self.new) - len(self.gone)

    @property
    def counts(self) -> dict:
        return {"new": len(self.new), "gone": len(self.gone), "deleted": len(self.deleted), "net": self.net}

    def as_dict(self) -> dict:
        return {"period": self.period, "new": [x.as_dict() for x in self.new],
                "gone": [x.as_dict() for x in self.gone], "deleted": [x.as_dict() for x in self.deleted],
                "counts": self.counts}


def _month_word(period: str) -> str:
    word = month_name_ru(int(period[5:7])).lower()
    return _GENITIVE.get(word, word)


def month_flow(period: str, rows: list, events: list, students: dict[str, str], groups: dict[str, str]) -> FlowReport:
    """rows — StudentGroup (все, с ушедшими); events — лента за месяц (ts, text); students/groups — id → имя."""
    by_student: dict[str, list] = {}
    for r in rows:
        by_student.setdefault(r.student_id, []).append(r)

    def gname(gid: str) -> str:
        return str(groups.get(gid, gid))

    def sname(sid: str) -> str:
        return str(students.get(sid, sid))

    removed: dict[str, set] = {}        # ученик → группы, откуда убран в этом месяце (строки уже нет)
    deleted: dict[str, str] = {}        # ученик → имя из события (у старых событий имени нет)
    created: set = set()
    came_at: dict[str, str] = {}        # ученик → день прихода (первое «добавлен в группу» / «Новый ученик» месяца)
    gone_at: dict[str, str] = {}        # ученик → день ухода (последнее «уходит / убран / переведён» не позже месяца)
    for e in events:
        ts = getattr(e, "ts", "") or ""
        if ts[:7] > period:
            continue
        text = getattr(e, "text", "") or ""
        day, in_month = ts[:10], ts[:7] == period
        if (m := _REMOVED.search(text)) or (m := _TRANSFER.search(text)):
            if in_month:
                removed.setdefault(m.group(1), set()).add(m.group(2))
            gone_at[m.group(1)] = max(gone_at.get(m.group(1), ""), day)
        elif m := _LEFT.search(text):
            gone_at[m.group(1)] = max(gone_at.get(m.group(1), ""), day)
        elif m := _DELETED.search(text):
            if in_month:
                deleted[m.group(1)] = (m.group(2) or "").strip()
        elif (m := _CREATED.search(text)) or (m := _ADDED.search(text)):
            if in_month:
                if text.startswith("Новый ученик"):
                    created.add(m.group(1))
                came_at[m.group(1)] = min(came_at.get(m.group(1), "9999"), day)

    joined: dict[str, list] = {}         # ученик → группы, куда пришёл в этом месяце
    left: dict[str, list] = {}           # ученик → группы, откуда ушёл с этого месяца (абонемент)
    for r in rows:
        if r.joined_period == period:
            joined.setdefault(r.student_id, []).append(r.group_id)
        if r.left_period == period:
            left.setdefault(r.student_id, []).append(r.group_id)
    for sid, gids in removed.items():
        for gid in gids:
            if gid not in left.get(sid, []):
                left.setdefault(sid, []).append(gid)

    def earlier(sid: str) -> bool:
        """Был ли ученик в какой-то группе до этого месяца (убран/переведён в этом месяце — значит, был)."""
        return sid in removed or any((not r.joined_period or r.joined_period < period) for r in by_student.get(sid, []))

    def remaining(sid: str) -> list:
        """Группы, где ученик остаётся после этого месяца."""
        gone_from = set(left.get(sid, []))
        return [r.group_id for r in by_student.get(sid, [])
                if r.group_id not in gone_from and r.is_active and not (r.joined_period and r.joined_period > period)]

    report = FlowReport(period)
    seen: set = set()
    for sid in sorted(set(joined) | set(left) | set(deleted) | created, key=lambda s: sname(s).lower()):
        if sid in seen:
            continue
        seen.add(sid)
        if sid in deleted and sid not in students:
            report.deleted.append(FlowItem(sid, deleted[sid] or sid, [gname(g) for g in left.get(sid, [])],
                                           "" if deleted[sid] else "имя не сохранилось"))
            continue
        came, went = joined.get(sid, []), left.get(sid, [])
        if came and not earlier(sid):
            report.new.append(FlowItem(sid, sname(sid), [gname(g) for g in came], date=came_at.get(sid, "")))
        elif came:
            note = f"из {', '.join(gname(g) for g in went)}" if went else "ещё одна группа"
            report.moves.append(FlowItem(sid, sname(sid), [gname(g) for g in came], note))
        elif went and remaining(sid):
            report.moves.append(FlowItem(sid, sname(sid), [gname(g) for g in went],
                                         f"ушёл из группы, остался в {', '.join(gname(g) for g in remaining(sid))}"))
        elif went:
            report.gone.append(FlowItem(sid, sname(sid), [gname(g) for g in went], f"с {_month_word(period)}",
                                        date=gone_at.get(sid, "")))
        elif sid in created and sid in students:
            report.new.append(FlowItem(sid, sname(sid), [], "без группы", date=came_at.get(sid, "")))
    for items in (report.new, report.gone):            # от сегодня к 1-му числу (решение владельца 22.10.2026); без даты — в конце
        items.sort(key=lambda x: (not x.date, x.date and "".join(chr(255 - ord(c)) for c in x.date), x.name.lower()))
    return report
