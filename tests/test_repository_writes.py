"""I1: запись по ключу под замком листа с проверкой по живому листу (BaseRepository._locked_row)."""
import asyncio
import logging

import pytest

import bot.repositories.base as base_mod
from bot.repositories.base import BaseRepository, _norm, _retry_wait
from bot.repositories.group_repo import GroupRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.repositories.student_group_repo import StudentGroupRepository
from bot.repositories.user_repo import UserRepository
from tests.fakes import FakeSheetsClient, FakeWorksheet, run

GROUP_HEADERS = ["group_id", "branch_id", "name", "created_at", "updated_at", "sort_order",
                 "billing_mode", "price_short", "duration_short", "price_full", "duration_full", "archived"]


@pytest.fixture(autouse=True)
def _clean_repo_state():
    BaseRepository._cache.clear()
    BaseRepository._locks.clear()
    BaseRepository._headers.clear()
    yield
    BaseRepository._cache.clear()
    BaseRepository._locks.clear()
    BaseRepository._headers.clear()


def _group_row(gid, name, archived=""):
    return [gid, "BRN-0001", name, "", "", 0, "none", 0, 35, 0, 60, archived]


def _groups_repo(n=4):
    ws = FakeWorksheet(GROUP_HEADERS, [_group_row(f"GRP-{i:04d}", f"Группа {i}") for i in range(1, n + 1)])
    return GroupRepository(FakeSheetsClient(ws), "groups"), ws


def test_norm_tolerates_float_formatted_ids():
    assert _norm(None) == "" and _norm(" STU-1 ") == "STU-1"
    assert _norm(826576855) == "826576855" and _norm("826576855.0") == "826576855" and _norm(850.0) == "850"
    assert _norm("2026-09") == "2026-09" and _norm("0850") == "0850"


def test_update_after_external_row_shift_writes_correct_row(caplog):
    """Кеш индексов устарел (кто-то вставил строку сверху) — запись всё равно попадает в свою строку."""
    repo, ws = _groups_repo()
    run(repo.get_all())                                   # прогреть кеш: GRP-0003 → строка 4
    ws.insert_row(_group_row("GRP-9999", "Вставили руками"), 2)   # теперь GRP-0003 → строка 5
    with caplog.at_level(logging.WARNING):
        assert run(repo.set_archived("GRP-0003", True)) is True
    assert ws.by_key("group_id", "GRP-0003")[11] == "1"
    assert ws.by_key("group_id", "GRP-0002")[11] == "" and ws.by_key("group_id", "GRP-9999")[11] == ""
    assert "лист изменился" in caplog.text


def test_missing_key_returns_false_without_touching_sheet():
    repo, ws = _groups_repo()
    assert run(repo.set_archived("GRP-0404", True)) is False
    assert not [c for c in ws.calls if c[0] in ("update_cell", "delete_rows")]


def test_gives_up_when_live_row_keeps_mismatching(caplog):
    repo, ws = _groups_repo()
    real_row_values = ws.row_values
    ws.row_values = lambda i: (real_row_values(i) if i == 1 else ["GRP-XXXX"])   # живой лист «всегда другой»
    with caplog.at_level(logging.ERROR):
        assert run(repo.set_archived("GRP-0002", True)) is False
    assert "запись отменена" in caplog.text
    assert not [c for c in ws.calls if c[0] == "update_cell"]


def test_concurrent_delete_and_update_are_serialized(monkeypatch):
    """Удаление строки сверху и обновление строки снизу параллельно: без замка второе
    записало бы по сдвинутому индексу в чужую строку."""
    repo, ws = _groups_repo()
    run(repo.get_all())

    async def yielding_to_thread(fn, *args, **kwargs):     # каждый вызов Sheets отдаёт управление другим корутинам
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fn(*args, **kwargs)
    monkeypatch.setattr(base_mod.asyncio, "to_thread", yielding_to_thread)

    async def scenario():
        return await asyncio.gather(repo.delete("GRP-0001"), repo.set_archived("GRP-0004", True))
    assert run(scenario()) == [True, True]
    assert [r[0] for r in ws.rows] == ["GRP-0002", "GRP-0003", "GRP-0004"]
    assert ws.by_key("group_id", "GRP-0004")[11] == "1"
    assert ws.by_key("group_id", "GRP-0002")[11] == "" and ws.by_key("group_id", "GRP-0003")[11] == ""


def test_delete_all_where_removes_every_matching_row():
    ws = FakeWorksheet(["student_id", "group_id", "joined_period", "left_period"], [
        ["STU-1", "GRP-1", "", ""], ["STU-2", "GRP-1", "", ""], ["STU-1", "GRP-2", "", ""],
        ["STU-3", "GRP-3", "", ""], ["STU-1", "GRP-3", "2026-09", ""],
    ])
    repo = StudentGroupRepository(FakeSheetsClient(ws), "student_groups")
    assert run(repo.remove_all_for_student("STU-1")) == 3
    assert [r[:2] for r in ws.rows] == [["STU-2", "GRP-1"], ["STU-3", "GRP-3"]]
    assert run(repo.remove_all_for_student("STU-404")) == 0
    assert run(repo.set_left_period("STU-3", "GRP-3", "2026-10")) is True
    assert ws.by_key("student_id", "STU-3")[3] == "2026-10"


def test_composite_key_updates_the_right_membership_row():
    ws = FakeWorksheet(["student_id", "group_id", "joined_period", "left_period"], [
        ["STU-1", "GRP-1", "", ""], ["STU-1", "GRP-2", "", ""],
    ])
    repo = StudentGroupRepository(FakeSheetsClient(ws), "student_groups")
    assert run(repo.set_joined_period("STU-1", "GRP-2", "2026-09")) is True
    assert ws.rows == [["STU-1", "GRP-1", "", ""], ["STU-1", "GRP-2", "2026-09", ""]]


def test_numeric_key_matches_float_formatted_cell():
    ws = FakeWorksheet(["user_id", "tg_id", "is_admin", "teacher_id"], [["USR-0001", 826576855.0, False, ""]])
    repo = UserRepository(FakeSheetsClient(ws), "users")
    assert run(repo.update_teacher_id(826576855, "TCH-0002")) is True
    assert ws.rows[0][3] == "TCH-0002"


def test_confirm_all_for_period_updates_only_pending_positive_rows():
    headers = ["payment_id", "student_id", "student_name", "period_month", "total_amount", "status", "paid_at",
               "confirmed_by_tg_id", "comment", "created_at", "updated_at", "teacher_id", "teacher_name", "payment_method"]
    ws = FakeWorksheet(headers, [
        ["PAY-1", "STU-1", "A", "2026-09", 1000, "pending", "", "", "", "", "", "T1", "", ""],
        ["PAY-2", "STU-1", "A", "2026-09", 500, "paid", "x", 7, "", "", "", "T2", "", "cash"],
        ["PAY-3", "STU-1", "A", "2026-09", 0, "pending", "", "", "", "", "", "T3", "", ""],
        ["PAY-4", "STU-2", "B", "2026-09", 900, "pending", "", "", "", "", "", "T1", "", ""],
    ])
    repo = PaymentRepository(FakeSheetsClient(ws), "student_payments")
    assert run(repo.confirm_all_for_period("STU-1", "2026-09", 7, "receipt_bank")) == 1
    assert ws.rows[0][5] == "paid" and ws.rows[0][13] == "receipt_bank" and ws.rows[0][7] == 7
    assert ws.rows[2][5] == "pending" and ws.rows[3][5] == "pending"


def _reads(ws):
    return sum(1 for c in ws.calls if c[0] == "get_all_records")


def test_writes_patch_cache_without_rereading_sheet():
    """После записи кеш правится на месте: следующее чтение не ходит в Sheets (экономия квоты)."""
    repo, ws = _groups_repo()
    run(repo.get_all())
    assert _reads(ws) == 1
    assert run(repo.set_archived("GRP-0002", True)) is True
    assert [g.archived for g in run(repo.get_all(include_archived=True))][1] is True
    assert run(repo.delete("GRP-0001")) is True
    assert [g.group_id for g in run(repo.get_all(include_archived=True))] == ["GRP-0002", "GRP-0003", "GRP-0004"]
    run(repo.add("BRN-0001", "Новая"))
    assert [g.name for g in run(repo.get_all(include_archived=True))][-1] == "Новая"
    assert _reads(ws) == 1                                   # ни одного лишнего чтения листа
    assert run(repo.update_name("GRP-0003", "Третья")) is True
    assert run(repo.get_by_id("GRP-0003")).name == "Третья"
    assert _reads(ws) == 1
    # и лист, и кеш согласованы
    assert [r[2] for r in ws.rows] == [g.name for g in run(repo.get_all(include_archived=True))]


def test_cache_patch_falls_back_to_invalidate_without_headers():
    repo, ws = _groups_repo()
    run(repo.get_all())
    BaseRepository._headers.pop("groups", None)            # заголовки ещё не читались
    run(repo._update_cell(2, 3, "X"))
    assert "groups" not in BaseRepository._cache             # кеш сброшен — следующее чтение с листа
    assert run(repo.get_by_id("GRP-0001")).name == "X"


def test_retry_wait_is_longer_for_quota_errors():
    assert [_retry_wait(429, a) for a in range(3)] == [10, 20, 40]
    assert [_retry_wait(503, a) for a in range(3)] == [5, 10, 20]
