"""Очередь решений (лист pending_actions): номера не задваиваются, задвоенные — всё равно закрываются."""
import asyncio

import pytest

from bot.repositories.base import BaseRepository
from bot.repositories.pending_action_repo import (
    DONE, KIND_CASH, OPEN, REJECTED, PendingActionRepository,
)
from tests.fakes import FakeSheetsClient, FakeWorksheet, run

HEADERS = ["action_id", "kind", "student_id", "student_name", "period_month", "amount", "method",
           "parent_addr", "file_id", "file_type", "comment", "created_at", "status", "decided_at", "decided_by_tg_id"]


@pytest.fixture(autouse=True)
def _clean_repo_state():
    for store in (BaseRepository._cache, BaseRepository._locks, BaseRepository._headers):
        store.clear()
    yield
    for store in (BaseRepository._cache, BaseRepository._locks, BaseRepository._headers):
        store.clear()


def _row(action_id, status=OPEN, name="Черба Руслана"):
    return [action_id, KIND_CASH, "STU-0017", name, "2026-09", 35800, "cash", "8684688985", "", "", "",
            "2026-09-21 09:10:22", status, "", ""]


def _repo(rows):
    ws = FakeWorksheet(HEADERS, rows)
    return PendingActionRepository(FakeSheetsClient(ws), "pending_actions"), ws


def test_concurrent_adds_get_distinct_ids():
    """Два тапа родителя в одну секунду — два разных номера, не один на двоих."""
    repo, ws = _repo([_row("ACT-000001", REJECTED)])

    async def burst():
        return await asyncio.gather(*(repo.add(KIND_CASH, "STU-0017", "Черба Руслана", "2026-09", 35800, "cash")
                                      for _ in range(3)))
    created = run(burst())
    ids = [a.action_id for a in created]
    assert ids == ["ACT-000002", "ACT-000003", "ACT-000004"]
    assert [r[0] for r in ws.rows] == ["ACT-000001", "ACT-000002", "ACT-000003", "ACT-000004"]


def test_duplicate_id_rows_all_get_closed():
    """Старое задвоение (три строки ACT-000002): «Отклонить» закрывает обе открытые копии."""
    repo, ws = _repo([_row("ACT-000002", REJECTED), _row("ACT-000002"), _row("ACT-000002"), _row("ACT-000003")])
    assert run(repo.get_by_id("ACT-000002")).status == OPEN        # открытая копия важнее закрытой
    assert run(repo.close("ACT-000002", REJECTED, 1)) is True
    assert [r[12] for r in ws.rows] == [REJECTED, REJECTED, REJECTED, OPEN]
    assert [a.action_id for a in run(repo.get_open())] == ["ACT-000003"]
    assert run(repo.close("ACT-000002", REJECTED, 1)) is False     # закрывать больше нечего


def test_claim_credits_once_even_with_duplicates():
    repo, ws = _repo([_row("ACT-000005"), _row("ACT-000005")])
    assert run(repo.claim("ACT-000005", DONE, 1)) is True
    assert run(repo.claim("ACT-000005", DONE, 2)) is False         # второй админ опоздал
    assert [r[12] for r in ws.rows] == [DONE, DONE] and [r[14] for r in ws.rows] == [1, 1]
