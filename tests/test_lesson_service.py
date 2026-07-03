"""Характеризующие тесты для LessonService.create_shared_individual().

Репозитории заменены in-memory фейками; async — через asyncio.run.
"""
import asyncio

from bot.models import Teacher
from bot.models.enums import LessonType
from bot.services import LessonService


def _run(coro):
    return asyncio.run(coro)


class _FakeLessonRepo:
    def __init__(self):
        self.added = []

    async def get_existing_ids(self):
        return [ls.lesson_id for ls in self.added]

    async def individual_lesson_exists(self, teacher_id, student_id, lesson_date):
        return any(
            ls.teacher_id == teacher_id and ls.date == lesson_date
            and student_id in (ls.student_1_id, ls.student_2_id,
                               ls.student_3_id, ls.student_4_id)
            for ls in self.added
        )

    async def add(self, lesson):
        self.added.append(lesson)


class _FakeSubmissionRepo:
    def __init__(self, submitted_periods=()):
        self._submitted = set(submitted_periods)  # {(teacher_id, period)}

    async def get_by_teacher_and_period(self, teacher_id, period_month):
        return object() if (teacher_id, period_month) in self._submitted else None


def _teacher():
    return Teacher("TCH-1", None, "Педагог", 500, 800, 900)


def _service(submitted=()):
    return LessonService(_FakeLessonRepo(), _FakeSubmissionRepo(submitted), None)


def test_shared_sorts_by_student_id_and_pads_slots():
    svc = _service()
    lesson = _run(svc.create_shared_individual(
        _teacher(), "2026-07-01", 60,
        students=[("STU-9", "Яшин"), ("STU-1", "Азов"), ("STU-5", "Мидин")],
    ))
    assert lesson.type == LessonType.INDIVIDUAL
    assert (lesson.student_1_id, lesson.student_2_id,
            lesson.student_3_id, lesson.student_4_id) == ("STU-1", "STU-5", "STU-9", None)
    assert (lesson.student_1_name, lesson.student_2_name,
            lesson.student_3_name, lesson.student_4_name) == ("Азов", "Мидин", "Яшин", None)


def test_shared_creates_single_lesson():
    svc = _service()
    repo = svc._lesson_repo
    _run(svc.create_shared_individual(
        _teacher(), "2026-07-01", 60,
        students=[("STU-1", "Азов"), ("STU-2", "Бобров")],
    ))
    assert len(repo.added) == 1


def test_shared_respects_period_lock():
    svc = _service(submitted={("TCH-1", "2026-07")})
    try:
        _run(svc.create_shared_individual(
            _teacher(), "2026-07-01", 60,
            students=[("STU-1", "Азов"), ("STU-2", "Бобров")],
        ))
        assert False, "ожидался PermissionError"
    except PermissionError:
        pass


def test_shared_duplicate_guard_applies_per_student():
    svc = _service()
    _run(svc.create_shared_individual(
        _teacher(), "2026-07-01", 60,
        students=[("STU-1", "Азов"), ("STU-2", "Бобров")],
    ))
    try:
        _run(svc.create_shared_individual(
            _teacher(), "2026-07-01", 45,
            students=[("STU-2", "Бобров"), ("STU-3", "Мидин")],
        ))
        assert False, "ожидался ValueError (дубль соло-занятия STU-2)"
    except ValueError:
        pass
