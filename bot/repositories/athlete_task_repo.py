"""Лист athlete_tasks: задания педагога спортсмену (упражнение + минуты).

Колонки: task_id | student_id | teacher_id | exercise | minutes | comment |
source | created_at | status | closed_at | batch_id | group_id | grade | grade_comment | graded_at.
Задание открыто до закрытия педагогом. Домашнее задание группе — строка на каждую ученицу с общим
batch_id (HW-…); оценка за выполнение закрывает задание (`scripts/setup_homework.py` — колонки 11–15).
"""
from __future__ import annotations
import logging
from typing import Optional

from bot.models.entities import AthleteTask
from bot.utils.ids import generate_homework_id, generate_task_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)


def _int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _row_to_task(row: dict) -> AthleteTask:
    return AthleteTask(
        task_id=str(row.get("task_id") or ""),
        student_id=str(row.get("student_id") or ""),
        teacher_id=str(row.get("teacher_id") or ""),
        exercise=str(row.get("exercise") or ""),
        minutes=_int(row.get("minutes")),
        comment=str(row.get("comment") or ""),
        source=str(row.get("source") or "teacher"),
        created_at=str(row.get("created_at") or ""),
        status=str(row.get("status") or "open"),
        closed_at=str(row.get("closed_at") or ""),
        batch_id=str(row.get("batch_id") or ""),
        group_id=str(row.get("group_id") or ""),
        grade=_int(row.get("grade")),
        grade_comment=str(row.get("grade_comment") or ""),
        graded_at=str(row.get("graded_at") or ""),
    )


def _task_to_row(t: AthleteTask) -> list:
    return [
        t.task_id, t.student_id, t.teacher_id, t.exercise, t.minutes,
        t.comment, t.source, t.created_at, t.status, t.closed_at,
        t.batch_id, t.group_id, t.grade or "", t.grade_comment, t.graded_at,
    ]


class AthleteTaskRepository(BaseRepository):
    async def get_all(self) -> list[AthleteTask]:
        return [_row_to_task(r) for r in await self._all_records() if r.get("task_id")]

    async def get_by_id(self, task_id: str) -> Optional[AthleteTask]:
        for t in await self.get_all():
            if t.task_id == task_id:
                return t
        return None

    async def get_for_student(self, student_id: str, only_open: bool = False) -> list[AthleteTask]:
        rows = [
            t for t in await self.get_all()
            if t.student_id == student_id and (not only_open or t.status == "open")
        ]
        rows.sort(key=lambda t: t.created_at)
        return rows

    async def add(
        self, student_id: str, teacher_id: str, exercise: str, minutes: int,
        comment: str = "", source: str = "teacher",
    ) -> AthleteTask:
        existing = [t.task_id for t in await self.get_all()]
        task = AthleteTask(
            task_id=generate_task_id(existing), student_id=student_id, teacher_id=teacher_id,
            exercise=exercise, minutes=minutes, comment=comment, source=source,
            created_at=now_str(),
        )
        await self._append_row(_task_to_row(task))
        logger.info("Задание %s: %s ← %s «%s» %d мин", task.task_id, student_id, teacher_id, exercise, minutes)
        return task

    async def add_batch(
        self, student_ids: list[str], teacher_id: str, exercise: str, minutes: int,
        comment: str = "", group_id: str = "",
    ) -> list[AthleteTask]:
        """Домашнее задание нескольким ученицам: одна выдача (общий batch_id), строки одним запросом.

        Номера выдаются под замком листа — две выдачи подряд не получат одинаковые TK-/HW-."""
        async with self._sheet_lock():
            all_tasks = await self.get_all()
            ids = [t.task_id for t in all_tasks]
            batch_id = generate_homework_id([t.batch_id for t in all_tasks if t.batch_id])
            now = now_str()
            tasks = []
            for sid in student_ids:
                task_id = generate_task_id(ids)
                ids.append(task_id)
                tasks.append(AthleteTask(
                    task_id=task_id, student_id=sid, teacher_id=teacher_id, exercise=exercise, minutes=minutes,
                    comment=comment, created_at=now, batch_id=batch_id, group_id=group_id,
                ))
            await self._append_rows([_task_to_row(t) for t in tasks])
        logger.info("Домашнее задание %s: %s → %d учениц «%s»", batch_id, teacher_id, len(tasks), exercise)
        return tasks

    async def grade(self, task_id: str, grade: int, comment: str = "") -> Optional[AthleteTask]:
        """Оценка за выполнение: ставит оценку и закрывает задание (повторно — меняет оценку)."""
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        async with self._locked_row(task_id=task_id) as row_idx:
            if row_idx is None:
                return None
            now = now_str()
            task.grade, task.grade_comment, task.graded_at = grade, comment, now
            if task.status != "closed":
                task.status, task.closed_at = "closed", now
            await self._update_row(row_idx, _task_to_row(task))
            return task

    async def close(self, task_id: str) -> Optional[AthleteTask]:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        async with self._locked_row(task_id=task_id) as row_idx:
            if row_idx is None:
                return None
            task.status, task.closed_at = "closed", now_str()
            await self._update_row(row_idx, _task_to_row(task))
            return task
