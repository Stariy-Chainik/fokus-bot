"""Дневник спортсмена: записи тренировок, задания педагога, оценки, рейтинг.

Чистые функции (`entry_points`, `compute_stats`, `compute_leaderboard`) не зависят
от хранилища и покрыты тестами; класс `DiaryService` собирает данные из репозиториев.

Рейтинг: очки = минуты × оценка (1–5). Запись без оценки временно считается
с коэффициентом UNGRADED_GRADE, после оценки пересчитывается. При фильтре по танцу
минуты записи делятся поровну между её темами.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from bot.models import Student, TrainingEntry, AthleteTask
from bot.repositories import (
    StudentRepository, StudentGroupRepository, GroupRepository,
    TrainingEntryRepository, AthleteTaskRepository,
)
from bot.services.visibility import TeacherVisibilityService
from bot.utils.diary_topics import topics_for_student

logger = logging.getLogger(__name__)

UNGRADED_GRADE = 3
GRADE_MIN, GRADE_MAX = 1, 5


@dataclass
class DiaryStats:
    sessions: int = 0
    total_minutes: int = 0
    by_topic: dict = field(default_factory=dict)   # тема → минуты (доля записи)
    graded: int = 0
    avg_grade: Optional[float] = None
    points: int = 0


@dataclass
class LeaderRow:
    student_id: str
    name: str
    points: int
    minutes: int
    sessions: int
    avg_grade: Optional[float]
    place: int = 0


# ─── Чистые функции ───────────────────────────────────────────────────────────

def topic_share(entry: TrainingEntry, topic: Optional[str]) -> float:
    """Доля записи, относящаяся к теме: 1.0 без фильтра, 1/n при n темах, 0 если темы нет."""
    if topic is None:
        return 1.0
    if not entry.topics or topic not in entry.topics:
        return 0.0
    return 1.0 / len(entry.topics)


def entry_points(entry: TrainingEntry, topic: Optional[str] = None) -> int:
    grade = entry.grade if entry.grade else UNGRADED_GRADE
    return round(entry.minutes * topic_share(entry, topic) * grade)


def compute_stats(entries: list[TrainingEntry]) -> DiaryStats:
    st = DiaryStats(sessions=len(entries))
    grades: list[int] = []
    for e in entries:
        st.total_minutes += e.minutes
        st.points += entry_points(e)
        if e.grade:
            grades.append(e.grade)
        if e.topics:
            share = e.minutes / len(e.topics)
            for t in e.topics:
                st.by_topic[t] = st.by_topic.get(t, 0) + share
    st.by_topic = {t: round(m) for t, m in sorted(st.by_topic.items(), key=lambda kv: -kv[1])}
    st.graded = len(grades)
    st.avg_grade = round(sum(grades) / len(grades), 1) if grades else None
    return st


def compute_leaderboard(
    entries: list[TrainingEntry], names: dict[str, str], topic: Optional[str] = None,
) -> list[LeaderRow]:
    """Строки рейтинга для всех участников из `names` (даже с нулём очков),
    отсортированные по очкам ↓, минутам ↓, имени. Места — плотные (равные очки = одно место)."""
    acc: dict[str, dict] = {sid: {"points": 0, "minutes": 0.0, "sessions": 0, "grades": []} for sid in names}
    for e in entries:
        if e.student_id not in acc:
            continue
        share = topic_share(e, topic)
        if share == 0.0:
            continue
        a = acc[e.student_id]
        a["points"] += entry_points(e, topic)
        a["minutes"] += e.minutes * share
        a["sessions"] += 1
        if e.grade:
            a["grades"].append(e.grade)
    rows = [
        LeaderRow(
            student_id=sid, name=names[sid], points=a["points"], minutes=round(a["minutes"]),
            sessions=a["sessions"],
            avg_grade=round(sum(a["grades"]) / len(a["grades"]), 1) if a["grades"] else None,
        )
        for sid, a in acc.items()
    ]
    rows.sort(key=lambda r: (-r.points, -r.minutes, r.name.lower()))
    place, prev = 0, None
    for r in rows:
        if prev is None or r.points != prev:
            place += 1
        r.place = place
        prev = r.points
    return rows


def place_icon(place: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, f"{place}.")


def period_of(d: date) -> str:
    return d.strftime("%Y-%m")


# ─── Сервис ───────────────────────────────────────────────────────────────────

class DiaryService:
    def __init__(
        self,
        entry_repo: TrainingEntryRepository,
        task_repo: AthleteTaskRepository,
        student_repo: StudentRepository,
        student_group_repo: StudentGroupRepository,
        group_repo: GroupRepository,
        visibility: TeacherVisibilityService,
    ) -> None:
        self._entries = entry_repo
        self._tasks = task_repo
        self._students = student_repo
        self._student_groups = student_group_repo
        self._groups = group_repo
        self._visibility = visibility

    # ── Спортсмены ────────────────────────────────────────────────────────────

    async def athlete_by_tg(self, tg_id: int) -> Optional[Student]:
        return await self._students.get_by_athlete_tg_id(tg_id)

    async def linked_athletes(self) -> list[Student]:
        """Все ученики со своим Telegram (участники рейтинга)."""
        sg_map = await self._student_groups.get_map_by_student()
        out = []
        for s in await self._students.get_all():
            if s.athlete_tg_id:
                s.group_ids = sg_map.get(s.student_id, [])
                out.append(s)
        out.sort(key=lambda s: s.name.lower())
        return out

    async def athletes_for_teacher(self, teacher_id: Optional[str], is_admin: bool) -> list[Student]:
        if is_admin or not teacher_id:
            return await self.linked_athletes() if is_admin else []
        return [s for s in await self._visibility.students_for_teacher(teacher_id) if s.athlete_tg_id]

    async def registration_candidates(self, query: str, athlete_group_ids: set[str]) -> list[Student]:
        """Ученики спортивных групп, чьё имя начинается с запроса (фамилия первая)."""
        member_ids: set[str] = set()
        for gid in athlete_group_ids:
            member_ids.update(await self._student_groups.get_students_for_group(gid))
        q = query.strip().lower()
        return sorted(
            (s for s in await self._students.get_all()
             if s.student_id in member_ids and s.name.lower().startswith(q)),
            key=lambda s: s.name.lower(),
        )

    async def group_names_all(self):
        return await self._groups.get_all()

    async def topics_for(self, student: Student) -> list[str]:
        gids = student.group_ids or await self._student_groups.get_groups_for_student(student.student_id)
        names = [g.name for g in await self._groups.get_all() if g.group_id in gids]
        return topics_for_student(names)

    # ── Записи ────────────────────────────────────────────────────────────────

    async def create_entry(
        self, student_id: str, date_str: str, minutes: int,
        topics: list[str], task_ids: list[str], comment: str = "",
    ) -> TrainingEntry:
        return await self._entries.add(student_id, date_str, minutes, topics, task_ids, comment)

    async def entry(self, entry_id: str) -> Optional[TrainingEntry]:
        return await self._entries.get_by_id(entry_id)

    async def entries_for_student(
        self, student_id: str, period: Optional[str] = None, days: Optional[int] = None,
    ) -> list[TrainingEntry]:
        rows = await self._entries.get_for_student(student_id)
        if period:
            rows = [e for e in rows if e.date[:7] == period]
        if days:
            since = (date.today() - timedelta(days=days - 1)).isoformat()
            rows = [e for e in rows if e.date >= since]
        return rows

    async def entries_for_period(self, period: str) -> list[TrainingEntry]:
        return [e for e in await self._entries.get_all() if e.date[:7] == period]

    async def delete_entry(self, entry_id: str, student_id: str) -> bool:
        """Спортсмен удаляет только свою запись и только пока нет оценки."""
        e = await self._entries.get_by_id(entry_id)
        if e is None or e.student_id != student_id or e.grade:
            return False
        return await self._entries.delete(entry_id)

    async def grade_entry(
        self, entry_id: str, grade: int, comment: str, graded_by: str,
    ) -> Optional[TrainingEntry]:
        if not GRADE_MIN <= grade <= GRADE_MAX:
            raise ValueError("Оценка должна быть от 1 до 5")
        return await self._entries.set_grade(entry_id, grade, comment, graded_by)

    async def unrated_counts(self, student_ids: list[str]) -> dict[str, int]:
        wanted = set(student_ids)
        counts: dict[str, int] = {}
        for e in await self._entries.get_all():
            if e.student_id in wanted and not e.grade:
                counts[e.student_id] = counts.get(e.student_id, 0) + 1
        return counts

    async def stats(self, student_id: str, period: str) -> DiaryStats:
        return compute_stats(await self.entries_for_student(student_id, period=period))

    # ── Задания ───────────────────────────────────────────────────────────────

    async def create_task(
        self, student_id: str, teacher_id: str, exercise: str, minutes: int, comment: str = "",
    ) -> AthleteTask:
        return await self._tasks.add(student_id, teacher_id, exercise, minutes, comment)

    async def task(self, task_id: str) -> Optional[AthleteTask]:
        return await self._tasks.get_by_id(task_id)

    async def tasks_map(self, student_id: str) -> dict[str, AthleteTask]:
        """Все задания ученика (и закрытые) — для расшифровки task_ids записи."""
        return {t.task_id: t for t in await self._tasks.get_for_student(student_id)}

    async def open_tasks(self, student_id: str) -> list[AthleteTask]:
        return await self._tasks.get_for_student(student_id, only_open=True)

    async def close_task(self, task_id: str) -> Optional[AthleteTask]:
        return await self._tasks.close(task_id)

    async def task_usage(self, student_id: str) -> dict[str, tuple[int, str]]:
        """task_id → (сколько раз отработано, дата последнего раза)."""
        usage: dict[str, tuple[int, str]] = {}
        for e in await self._entries.get_for_student(student_id):
            for tid in e.task_ids:
                cnt, last = usage.get(tid, (0, ""))
                usage[tid] = (cnt + 1, max(last, e.date))
        return usage

    async def recent_exercises(self, teacher_id: str, limit: int = 8) -> list[str]:
        """Недавние упражнения педагога — кнопки быстрого выбора при новом задании."""
        seen: list[str] = []
        tasks = sorted(
            (t for t in await self._tasks.get_all() if t.teacher_id == teacher_id),
            key=lambda t: t.created_at, reverse=True,
        )
        for t in tasks:
            if t.exercise and t.exercise not in seen:
                seen.append(t.exercise)
            if len(seen) >= limit:
                break
        return seen

    # ── Рейтинг ───────────────────────────────────────────────────────────────

    async def leaderboard(self, period: str, topic: Optional[str] = None) -> list[LeaderRow]:
        names = {s.student_id: s.name for s in await self.linked_athletes()}
        return compute_leaderboard(await self.entries_for_period(period), names, topic)
