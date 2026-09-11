"""Выход из группы: абонемент — пометка «ушёл с месяца», остальные — удаление строки."""
import asyncio
from types import SimpleNamespace

from bot.models import StudentGroup
from bot.models.enums import GroupBillingMode
from bot.services.membership import leave_group, is_subscription, leave_options


def _run(coro):
    return asyncio.run(coro)


class _GroupRepo:
    def __init__(self, mode):
        self._mode = mode

    async def get_by_id(self, gid):
        return SimpleNamespace(group_id=gid, name="Группа", billing_mode=self._mode)


class _SGRepo:
    def __init__(self, rows):
        self.rows = list(rows)
        self.removed, self.marked = [], []

    async def set_left_period(self, sid, gid, period):
        for i, r in enumerate(self.rows):
            if r.student_id == sid and r.group_id == gid:
                self.rows[i] = StudentGroup(sid, gid, r.joined_period, period)
                self.marked.append((sid, gid, period))
                return True
        return False

    async def remove(self, sid, gid):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not (r.student_id == sid and r.group_id == gid)]
        if len(self.rows) < before:
            self.removed.append((sid, gid))
            return True
        return False


def test_subscription_group_marks_left_period_and_keeps_row():
    sg = _SGRepo([StudentGroup("STU-A", "GRP-1", "2026-04")])
    res = _run(leave_group("STU-A", "GRP-1", "2026-09",
                           _GroupRepo(GroupBillingMode.SUBSCRIPTION), sg))
    assert res == "marked" and sg.removed == [] and sg.marked == [("STU-A", "GRP-1", "2026-09")]
    row = sg.rows[0]
    assert row.left_period == "2026-09" and not row.is_active
    assert row.covers("2026-08") and not row.covers("2026-09")


def test_per_visit_group_removes_row_as_before():
    sg = _SGRepo([StudentGroup("STU-A", "GRP-2", "2026-04")])
    res = _run(leave_group("STU-A", "GRP-2", "2026-09",
                           _GroupRepo(GroupBillingMode.PER_VISIT), sg))
    assert res == "removed" and sg.rows == [] and sg.marked == []


def test_missing_membership_reports_missing():
    sg = _SGRepo([])
    assert _run(leave_group("STU-A", "GRP-1", "2026-09",
                            _GroupRepo(GroupBillingMode.SUBSCRIPTION), sg)) == "missing"
    assert _run(leave_group("STU-A", "GRP-2", "2026-09",
                            _GroupRepo(GroupBillingMode.NONE), sg)) == "missing"


def test_is_subscription_flag():
    assert _run(is_subscription("GRP-1", _GroupRepo(GroupBillingMode.SUBSCRIPTION)))
    assert not _run(is_subscription("GRP-1", _GroupRepo(GroupBillingMode.NONE)))


def test_leave_options_offer_this_and_next_month():
    opts = leave_options()
    assert len(opts) == 2 and opts[0][0] < opts[1][0]
    assert all(len(p) == 7 for p, _ in opts)
