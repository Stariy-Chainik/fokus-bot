"""Кеш листов (14.10.2026): устаревший отдаётся сразу и дочитывается в фоне, параллельные запросы читают лист
один раз, чтение во время записи не затирает запись в кеше."""
import asyncio
import threading
import time

import pytest

import bot.repositories.base as base_mod
from bot.repositories.base import BaseRepository
from bot.repositories.group_repo import GroupRepository
from tests.fakes import FakeSheetsClient, FakeWorksheet
from tests.test_repository_writes import GROUP_HEADERS, _group_row


@pytest.fixture(autouse=True)
def _clean():
    for store in (BaseRepository._cache, BaseRepository._locks, BaseRepository._headers, BaseRepository._inflight):
        store.clear()
    yield
    for store in (BaseRepository._cache, BaseRepository._locks, BaseRepository._headers, BaseRepository._inflight):
        store.clear()


class SlowSheet(FakeWorksheet):
    def __init__(self, *a, delay=0.2, **k):
        super().__init__(*a, **k)
        self.delay, self.reads = delay, 0
        self.lock = threading.Lock()

    def get_all_records(self, default_blank=None):
        with self.lock:
            self.reads += 1
        snapshot = super().get_all_records(default_blank)        # строки на момент запроса, ответ — после паузы
        time.sleep(self.delay)
        return snapshot


def _repo(delay=0.2):
    ws = SlowSheet(GROUP_HEADERS, [_group_row("GRP-0001", "Первая")], delay=delay)
    return GroupRepository(FakeSheetsClient(ws), "groups"), ws


def test_parallel_requests_read_sheet_once():
    repo, ws = _repo()

    async def run():
        return await asyncio.gather(*(repo.get_all() for _ in range(8)))
    results = asyncio.run(run())
    assert ws.reads == 1 and all(len(r) == 1 for r in results)


def test_stale_cache_served_immediately_and_refreshed_in_background(monkeypatch):
    repo, ws = _repo(delay=0.3)

    async def run():
        await repo.get_all()
        ws.rows.append(_group_row("GRP-0002", "Вторая"))           # лист изменили руками
        name = "groups"
        recs, _ = BaseRepository._cache[name]
        BaseRepository._cache[name] = (recs, time.monotonic() - base_mod._CACHE_TTL - 1)   # кеш устарел
        t = time.perf_counter()
        stale = await repo.get_all()
        took = time.perf_counter() - t
        await asyncio.sleep(0.5)                                   # фоновое чтение закончилось
        fresh = await repo.get_all()
        return len(stale), took, len(fresh)
    n_stale, took, n_fresh = asyncio.run(run())
    assert n_stale == 1 and took < 0.1 and n_fresh == 2 and ws.reads == 2


def test_very_old_cache_waits_for_fresh_data():
    repo, ws = _repo(delay=0.05)

    async def run():
        await repo.get_all()
        ws.rows.append(_group_row("GRP-0002", "Вторая"))
        recs, _ = BaseRepository._cache["groups"]
        BaseRepository._cache["groups"] = (recs, time.monotonic() - base_mod._STALE_MAX - 1)
        return len(await repo.get_all())
    assert asyncio.run(run()) == 2


def test_write_during_read_is_not_lost():
    """Чтение началось до записи и вернуло старые строки — оно не должно затереть записанное в кеше."""
    repo, ws = _repo(delay=0.3)

    async def run():
        await repo.get_all()
        recs, _ = BaseRepository._cache["groups"]
        BaseRepository._cache["groups"] = (recs, time.monotonic() - base_mod._STALE_MAX - 1)
        reader = asyncio.create_task(repo.get_all())              # долгое чтение листа
        await asyncio.sleep(0.05)
        BaseRepository._headers["groups"] = list(GROUP_HEADERS)
        ws.rows.append(_group_row("GRP-0003", "Новая"))           # запись в лист во время чтения
        repo._patch_cache(0, values=_group_row("GRP-0003", "Новая"), append=True)
        await reader
        return [g.group_id for g in await repo.get_all(include_archived=True)]
    assert "GRP-0003" in asyncio.run(run())


def test_ledger_map_memoized_until_data_changes(monkeypatch):
    """Карта начислений считается один раз на версию данных; любая запись в лист — пересчёт."""
    from bot.services.payment_service import PaymentService

    class Repo:
        def __init__(self, name):
            self._sheet_name = name

        async def get_all(self, *a, **k):
            return []

    calls = []
    svc = PaymentService(Repo("student_payments"), Repo("lessons"), Repo("teachers"), group_repo=Repo("groups"),
                         student_group_repo=Repo("student_groups"), subscription_override_repo=Repo("overrides"),
                         student_repo=Repo("students"))

    async def fake_compute(since, until):
        calls.append((since, until))
        return {("STU-1", "TCH-1", "2026-10"): (100, 0)}
    monkeypatch.setattr(svc, "_compute_ledger_map", fake_compute)

    async def run():
        a = await svc.compute_ledger_map("2026-10", "2026-10")
        a[("x", "y", "z")] = (1, 1)                                # вызывающий может менять свою копию
        b = await svc.compute_ledger_map("2026-10", "2026-10")
        BaseRepository._gen["student_payments"] = BaseRepository._gen.get("student_payments", 0) + 1   # запись оплаты
        c = await svc.compute_ledger_map("2026-10", "2026-10")
        return a, b, c
    a, b, c = asyncio.run(run())
    assert len(calls) == 2 and ("x", "y", "z") not in b and c == b


def test_worksheet_lookup_is_retried_on_quota(monkeypatch):
    """Первое обращение к листу читает описание таблицы — при 429 повторяем, а не роняем запрос."""
    import gspread
    from types import SimpleNamespace as NS
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)
    ws = FakeWorksheet(GROUP_HEADERS, [_group_row("GRP-0001", "Первая")])

    class Client:
        calls = 0

        def get_worksheet(self, name):
            Client.calls += 1
            if Client.calls == 1:
                resp = NS(status_code=429, json=lambda: {"error": {"code": 429, "message": "Quota exceeded", "status": "x"}},
                          text="quota")
                raise gspread.exceptions.APIError(resp)
            return ws
    repo = GroupRepository(Client(), "groups")
    assert len(asyncio.run(repo.get_all())) == 1 and Client.calls == 2


def test_background_refresh_tracks_strong_task_reference():
    """Фоновое обновление кеша хранит сильную ссылку на задачу до завершения и чистит после."""
    repo, _ws = _repo(delay=0.15)

    async def run():
        await repo.get_all()
        recs, _ = BaseRepository._cache["groups"]
        BaseRepository._cache["groups"] = (recs, time.monotonic() - base_mod._CACHE_TTL - 1)
        await repo.get_all()
        active_during = len(BaseRepository._bg_tasks)
        await asyncio.gather(*list(BaseRepository._bg_tasks))
        await asyncio.sleep(0)
        return active_during, len(BaseRepository._bg_tasks)

    during, after = asyncio.run(run())
    assert during == 1 and after == 0


def test_sheets_client_initializes_once_under_parallel_threads(monkeypatch):
    """Параллельный прогрев (4+ потока) не должен вызывать authorize()/open_by_key() несколько раз."""
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from bot.repositories import sheets_client as sc_mod

    counts = {"creds": 0, "auth": 0, "open": 0, "ws": 0}
    cnt_lock = threading.Lock()

    class FakeSpreadsheet:
        def worksheet(self, name):
            time.sleep(0.05)
            with cnt_lock:
                counts["ws"] += 1
            return SimpleNamespace(title=name)

    class FakeGspreadClient:
        def open_by_key(self, key):
            time.sleep(0.05)
            with cnt_lock:
                counts["open"] += 1
            return FakeSpreadsheet()

    def fake_creds(info, scopes):
        with cnt_lock:
            counts["creds"] += 1
        return SimpleNamespace()

    def fake_authorize(creds):
        time.sleep(0.05)
        with cnt_lock:
            counts["auth"] += 1
        return FakeGspreadClient()

    monkeypatch.setattr(sc_mod.Credentials, "from_service_account_info", fake_creds)
    monkeypatch.setattr(sc_mod.gspread, "authorize", fake_authorize)

    cfg = SimpleNamespace(google_credentials_dict={}, spreadsheet_id="sheet-1")
    client = sc_mod.SheetsClient(cfg)  # type: ignore[arg-type]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.get_worksheet("groups"), range(8)))

    assert len({id(w) for w in results}) == 1
    assert counts == {"creds": 1, "auth": 1, "open": 1, "ws": 1}


def test_student_repo_parsed_cache_isolates_group_ids_mutation():
    """StudentRepository кеширует разбор, но мутация s.group_ids не должна утекать между вызовами."""
    from bot.repositories.student_repo import StudentRepository

    headers = ["student_id", "name", "partner_id", "group_id", "group_tier", "client_id", "parent_tg_ids"]
    ws = FakeWorksheet(headers, [["STU-0001", "Иванов Иван", "", "", "full", "", ""]])
    repo = StudentRepository(FakeSheetsClient(ws), "students")

    async def run():
        first = await repo.get_all()
        first[0].group_ids = ["GRP-9999"]
        second = await repo.get_all()
        return second[0].group_ids

    assert asyncio.run(run()) == []


def test_group_repo_add_does_not_collide_with_archived_group_id():
    """Архивная группа GRP-0001 не должна приводить к выдаче того же номера новой группе."""
    ws = FakeWorksheet(GROUP_HEADERS, [_group_row("GRP-0001", "Старая", archived="1")])
    repo = GroupRepository(FakeSheetsClient(ws), "groups")
    created = asyncio.run(repo.add("BRN-0001", "Новая"))
    assert created.group_id == "GRP-0002"


def test_group_repo_update_billing_uses_single_batch_update():
    """Обновление биллинга группы идёт одним batch_update вместо 6 последовательных update_cell."""
    from bot.models.enums import GroupBillingMode

    ws = FakeWorksheet(GROUP_HEADERS, [_group_row("GRP-0001", "Первая")])
    repo = GroupRepository(FakeSheetsClient(ws), "groups")
    assert asyncio.run(repo.update_billing("GRP-0001", GroupBillingMode.PER_VISIT, 500, 35, 850, 60)) is True
    batches = [c for c in ws.calls if c[0] == "batch_update"]
    assert len(batches) == 1 and len(batches[0][1]) == 6
