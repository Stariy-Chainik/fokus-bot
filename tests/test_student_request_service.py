"""Характеризующие тесты для StudentRequestService.

Репозитории заменены in-memory фейками; async — через asyncio.run.
"""
import asyncio

from bot.models import Student, StudentRequest
from bot.models.enums import RequestStatus
from bot.services import StudentRequestService, LinkExistingOutcome


def _run(coro):
    return asyncio.run(coro)


def _request(**over):
    base = dict(
        request_id="REQ-1", teacher_id="TCH-1", teacher_tg_id=100,
        teacher_name="Педагог", student_name="Новиков Ник",
        group_id="GRP-0001", status=RequestStatus.PENDING,
        created_at="2026-07-01 10:00:00",
    )
    base.update(over)
    return StudentRequest(**base)


class _FakeRequestRepo:
    def __init__(self, requests):
        self._requests = {r.request_id: r for r in requests}

    async def mark_resolved(self, request_id, status, resolved_by_tg_id):
        req = self._requests.get(request_id)
        if req is None or req.status != RequestStatus.PENDING:
            return False
        req.status = status
        req.resolved_by_tg_id = resolved_by_tg_id
        return True


class _FakeStudentRepo:
    def __init__(self):
        self.added = []

    async def add(self, name):
        student = Student(f"STU-NEW-{len(self.added) + 1}", name)
        self.added.append(student)
        return student


class _FakeStudentGroupRepo:
    def __init__(self, student_to_groups=None):
        self._s2g = dict(student_to_groups or {})
        self.added = []

    async def get_groups_for_student(self, student_id):
        return list(self._s2g.get(student_id, []))

    async def add(self, student_id, group_id):
        self._s2g.setdefault(student_id, []).append(group_id)
        self.added.append((student_id, group_id))


def _service(requests, student_to_groups=None):
    req_repo = _FakeRequestRepo(requests)
    student_repo = _FakeStudentRepo()
    sg_repo = _FakeStudentGroupRepo(student_to_groups)
    return StudentRequestService(req_repo, student_repo, sg_repo), student_repo, sg_repo


# ─── approve_create ──────────────────────────────────────────────────────────

def test_approve_create_creates_student_and_group_link():
    req = _request()
    svc, student_repo, sg_repo = _service([req])
    student = _run(svc.approve_create(req, resolved_by_tg_id=999))
    assert student is not None and student.name == "Новиков Ник"
    assert req.status == RequestStatus.APPROVED
    assert sg_repo.added == [(student.student_id, "GRP-0001")]


def test_approve_create_without_group_skips_group_link():
    req = _request(group_id="")
    svc, student_repo, sg_repo = _service([req])
    student = _run(svc.approve_create(req, resolved_by_tg_id=999))
    assert student is not None
    assert sg_repo.added == []


def test_approve_create_race_returns_none_and_creates_nothing():
    req = _request(status=RequestStatus.APPROVED)  # уже обработана параллельно
    svc, student_repo, sg_repo = _service([req])
    assert _run(svc.approve_create(req, resolved_by_tg_id=999)) is None
    assert student_repo.added == []
    assert sg_repo.added == []


# ─── approve_link_existing ───────────────────────────────────────────────────

def test_link_existing_already_in_group():
    req = _request()
    svc, _, sg_repo = _service([req], {"STU-1": ["GRP-0001"]})
    outcome = _run(svc.approve_link_existing(req, "STU-1", resolved_by_tg_id=999))
    assert outcome is LinkExistingOutcome.ALREADY_IN_GROUP
    assert sg_repo.added == []  # повторная запись не создаётся
    assert req.status == RequestStatus.APPROVED


def test_link_existing_added_to_group():
    req = _request()
    svc, _, sg_repo = _service([req], {"STU-1": ["GRP-OTHER"]})
    outcome = _run(svc.approve_link_existing(req, "STU-1", resolved_by_tg_id=999))
    assert outcome is LinkExistingOutcome.ADDED_TO_GROUP
    assert sg_repo.added == [("STU-1", "GRP-0001")]


def test_link_existing_no_group_in_request():
    req = _request(group_id="")
    svc, _, sg_repo = _service([req], {"STU-1": ["GRP-OTHER"]})
    outcome = _run(svc.approve_link_existing(req, "STU-1", resolved_by_tg_id=999))
    assert outcome is LinkExistingOutcome.NO_GROUP
    assert sg_repo.added == []


def test_link_existing_race_returns_none_and_writes_nothing():
    req = _request(status=RequestStatus.REJECTED)
    svc, _, sg_repo = _service([req])
    assert _run(svc.approve_link_existing(req, "STU-1", resolved_by_tg_id=999)) is None
    assert sg_repo.added == []
