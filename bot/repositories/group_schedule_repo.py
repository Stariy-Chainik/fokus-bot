"""Лист `group_schedule` — расписание групп: по нему бот напоминает педагогам отметить занятие.

Колонки: slot_id (SCH-) · group_id · weekday (1 = пн … 7 = вс) · start · end (ЧЧ:ММ) · teacher_id (пусто —
педагог выбирается по недавним занятиям группы, см. bot/services/lesson_reminders.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import BaseRepository

HEADER = ["slot_id", "group_id", "weekday", "start", "end", "teacher_id"]


@dataclass(frozen=True)
class ScheduleSlot:
    slot_id: str
    group_id: str
    weekday: int
    start: str
    end: str
    teacher_id: str = ""


def _row(row: dict) -> ScheduleSlot:
    return ScheduleSlot(str(row.get("slot_id") or ""), str(row.get("group_id") or ""), int(row.get("weekday") or 0),
                        str(row.get("start") or ""), str(row.get("end") or ""), str(row.get("teacher_id") or ""))


class GroupScheduleRepository(BaseRepository):
    async def get_all(self) -> list[ScheduleSlot]:
        return [_row(r) for r in await self._all_records() if r.get("slot_id")]

    async def get_for_group(self, group_id: str) -> list[ScheduleSlot]:
        return sorted((s for s in await self.get_all() if s.group_id == group_id), key=lambda s: (s.weekday, s.start))

    async def add(self, group_id: str, weekday: int, start: str, end: str, teacher_id: str = "") -> ScheduleSlot:
        async with self._sheet_lock():
            nums = [int(s.slot_id[4:]) for s in await self.get_all() if s.slot_id[4:].isdigit()]
            slot = ScheduleSlot(f"SCH-{max(nums, default=0) + 1:05d}", group_id, weekday, start, end, teacher_id)
            await self._append_row([slot.slot_id, group_id, weekday, start, end, teacher_id])
        return slot

    async def delete(self, slot_id: str) -> bool:
        async with self._locked_row(slot_id=slot_id) as row_idx:
            if row_idx is None:
                return False
            await self._delete_row(row_idx)
            return True
