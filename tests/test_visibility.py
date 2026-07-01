"""Характеризующие тесты для TeacherVisibilityService.

Видимость = пересечение teacher_groups ∩ student_groups. Репозитории заменены
in-memory фейками; async-методы прогоняются через asyncio.run (без pytest-asyncio).
"""
import asyncio

from bot.models import Student, StudentGroup
from bot.services.visibility import TeacherVisibilityService


def _run(coro):
    return asyncio.run(coro)


class _FakeStudentRepo:
    def __init__(self, students):
        self._students = students

    async def get_all(self):
        # Свежие копии, чтобы мутация group_ids не текла между тестами.
        return [
            Student(student_id=s.student_id, name=s.name)
            for s in self._students
        ]


class _FakeTeacherGroupRepo:
    def __init__(self, teacher_to_groups):
        self._t2g = teacher_to_groups
        self._g2t = {}
        for tid, gids in teacher_to_groups.items():
            for gid in gids:
                self._g2t.setdefault(gid, []).append(tid)

    async def get_groups_for_teacher(self, teacher_id):
        return list(self._t2g.get(teacher_id, []))

    async def get_teachers_for_group(self, group_id):
        return list(self._g2t.get(group_id, []))


class _FakeStudentGroupRepo:
    def __init__(self, student_to_groups):
        self._s2g = student_to_groups
        self._all = [
            StudentGroup(student_id=sid, group_id=gid)
            for sid, gids in student_to_groups.items()
            for gid in gids
        ]

    async def get_all(self):
        return list(self._all)

    async def get_groups_for_student(self, student_id):
        return list(self._s2g.get(student_id, []))

    async def get_students_for_group(self, group_id):
        return [sid for sid, gids in self._s2g.items() if group_id in gids]


def _service():
    students = [
        Student("STU-1", "Bravo"),
        Student("STU-2", "Alpha"),
        Student("STU-3", "Charlie"),
        Student("STU-4", "Delta"),
    ]
    teacher_groups = {"TCH-1": ["G1"], "TCH-2": ["G2"], "TCH-3": []}
    student_groups = {
        "STU-1": ["G1"],
        "STU-2": ["G1", "G2"],
        "STU-3": ["G2"],
        "STU-4": [],
    }
    return TeacherVisibilityService(
        _FakeStudentRepo(students),
        _FakeTeacherGroupRepo(teacher_groups),
        _FakeStudentGroupRepo(student_groups),
    )


def test_students_for_teacher_intersection_sorted_by_name():
    svc = _service()
    result = _run(svc.students_for_teacher("TCH-1"))
    # G1 → STU-1(Bravo), STU-2(Alpha); отсортировано по имени.
    assert [s.student_id for s in result] == ["STU-2", "STU-1"]
    # group_ids проставлены.
    assert result[0].group_ids == ["G1", "G2"]


def test_students_for_teacher_without_groups_empty():
    assert _run(_service().students_for_teacher("TCH-3")) == []


def test_is_visible():
    svc = _service()
    assert _run(svc.is_visible("TCH-1", "STU-1")) is True
    assert _run(svc.is_visible("TCH-1", "STU-3")) is False
    assert _run(svc.is_visible("TCH-1", "STU-4")) is False


def test_teachers_for_student_sorted_unique():
    svc = _service()
    assert _run(svc.teachers_for_student("STU-2")) == ["TCH-1", "TCH-2"]
    assert _run(svc.teachers_for_student("STU-4")) == []


def test_students_in_group_only_for_own_group():
    svc = _service()
    in_g1 = _run(svc.students_in_group_for_teacher("TCH-1", "G1"))
    assert [s.student_id for s in in_g1] == ["STU-2", "STU-1"]
    # G2 не входит в группы TCH-1 → пусто.
    assert _run(svc.students_in_group_for_teacher("TCH-1", "G2")) == []
