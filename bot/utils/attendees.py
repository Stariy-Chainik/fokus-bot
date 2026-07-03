"""
Парсер/сериализатор поля Lesson.attendees.

Два формата в одном поле:
  • старый CSV:       "STU-0001,STU-0002"       — только id, длительность = duration_min занятия, сумма = 0.
  • расширенный:      "STU-0001:60:850,STU-0002:35:500"
                      id : duration_min : amount_snapshot (рубли)

Используется:
  - record_lesson при записи группового занятия с billing_mode=per_visit (запись в новом формате);
  - биллинг-сервис при расчёте счёта ученика (чтение).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.models.enums import GroupBillingMode, StudentGroupTier

if TYPE_CHECKING:
    from bot.models import Group


@dataclass(frozen=True)
class AttendeeEntry:
    student_id: str
    duration_min: int
    amount: int  # рубли; 0 для пробных или неоплачиваемых групп


def parse_attendees(raw: str | None, default_duration: int = 60) -> list[AttendeeEntry]:
    """
    Разбирает attendees. Пустая строка/None → пустой список.
    Старый формат (нет ':') → duration=default_duration, amount=0.
    """
    if not raw:
        return []
    out: list[AttendeeEntry] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        sid = parts[0].strip()
        if not sid:
            continue
        try:
            dur = int(parts[1]) if len(parts) > 1 and parts[1] else default_duration
        except ValueError:
            dur = default_duration
        try:
            amt = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        except ValueError:
            amt = 0
        out.append(AttendeeEntry(student_id=sid, duration_min=dur, amount=amt))
    return out


def serialize_attendees(entries: list[AttendeeEntry]) -> str:
    """Всегда пишем расширенный формат id:duration:amount."""
    return ",".join(f"{e.student_id}:{e.duration_min}:{e.amount}" for e in entries)


def attendee_ids(raw: str | None) -> list[str]:
    """Быстрый список id без полного разбора — для мест, где нужны только ученики."""
    return [e.student_id for e in parse_attendees(raw)]


def build_group_attendees_csv(
    group: "Group | None", attendee_ids: list[str], tiers: dict[str, str],
) -> str | None:
    """Собрать attendees для группового занятия на момент записи.

    PER_VISIT-группа → расширенный формат со снапшотом тарифа: по tier
    ученика (default FULL) берутся duration/price short|full из группы.
    Иначе — старый CSV из id, либо None при пустом списке.
    """
    if attendee_ids and group is not None and group.billing_mode == GroupBillingMode.PER_VISIT:
        entries: list[AttendeeEntry] = []
        for sid in attendee_ids:
            tier = tiers.get(sid, StudentGroupTier.FULL.value)
            if tier == StudentGroupTier.SHORT.value:
                dur = group.duration_short
                amt = group.price_short
            else:
                dur = group.duration_full
                amt = group.price_full
            entries.append(AttendeeEntry(
                student_id=sid, duration_min=dur, amount=amt,
            ))
        return serialize_attendees(entries)
    return ",".join(attendee_ids) if attendee_ids else None
