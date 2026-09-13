"""Лист training_entries: дневник спортсмена (самостоятельные тренировки).

Колонки: entry_id | student_id | date | minutes | topics | task_ids | comment |
created_at | grade | grade_comment | graded_by | graded_at.
Списки (topics, task_ids) хранятся через `|` — запятая ломается в русской локали Sheets.
"""
from __future__ import annotations
import logging
from typing import Optional

from bot.models.entities import TrainingEntry
from bot.utils.ids import generate_training_entry_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

_SEP = "|"


def _int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _split(value) -> list[str]:
    return [x.strip() for x in str(value or "").split(_SEP) if x.strip()]


def _row_to_entry(row: dict) -> TrainingEntry:
    grade = _int(row.get("grade"))
    return TrainingEntry(
        entry_id=str(row.get("entry_id") or ""),
        student_id=str(row.get("student_id") or ""),
        date=str(row.get("date") or "")[:10],
        minutes=_int(row.get("minutes")),
        topics=_split(row.get("topics")),
        task_ids=_split(row.get("task_ids")),
        comment=str(row.get("comment") or ""),
        created_at=str(row.get("created_at") or ""),
        grade=grade or None,
        grade_comment=str(row.get("grade_comment") or ""),
        graded_by=str(row.get("graded_by") or ""),
        graded_at=str(row.get("graded_at") or ""),
    )


def _entry_to_row(e: TrainingEntry) -> list:
    return [
        e.entry_id, e.student_id, e.date, e.minutes,
        _SEP.join(e.topics), _SEP.join(e.task_ids), e.comment, e.created_at,
        e.grade or "", e.grade_comment, e.graded_by, e.graded_at,
    ]


class TrainingEntryRepository(BaseRepository):
    async def get_all(self) -> list[TrainingEntry]:
        return [_row_to_entry(r) for r in await self._all_records() if r.get("entry_id")]

    async def get_by_id(self, entry_id: str) -> Optional[TrainingEntry]:
        for e in await self.get_all():
            if e.entry_id == entry_id:
                return e
        return None

    async def get_for_student(self, student_id: str) -> list[TrainingEntry]:
        rows = [e for e in await self.get_all() if e.student_id == student_id]
        rows.sort(key=lambda e: (e.date, e.created_at), reverse=True)
        return rows

    async def add(
        self, student_id: str, date: str, minutes: int,
        topics: list[str], task_ids: list[str], comment: str = "",
    ) -> TrainingEntry:
        existing = [e.entry_id for e in await self.get_all()]
        entry = TrainingEntry(
            entry_id=generate_training_entry_id(existing), student_id=student_id,
            date=date, minutes=minutes, topics=list(topics), task_ids=list(task_ids),
            comment=comment, created_at=now_str(),
        )
        await self._append_row(_entry_to_row(entry))
        logger.info("Тренировка %s: %s %s %d мин %s", entry.entry_id, student_id, date, minutes, topics)
        return entry

    async def set_grade(
        self, entry_id: str, grade: int, grade_comment: str, graded_by: str,
    ) -> Optional[TrainingEntry]:
        entry = await self.get_by_id(entry_id)
        if entry is None:
            return None
        async with self._locked_row(entry_id=entry_id) as row_idx:
            if row_idx is None:
                return None
            entry.grade, entry.grade_comment = grade, grade_comment
            entry.graded_by, entry.graded_at = graded_by, now_str()
            await self._update_row(row_idx, _entry_to_row(entry))
            logger.info("Оценка %s: %d (%s)", entry_id, grade, graded_by)
            return entry

    async def delete(self, entry_id: str) -> bool:
        async with self._locked_row(entry_id=entry_id) as row_idx:
            if row_idx is None:
                return False
            await self._delete_row(row_idx)
            return True
