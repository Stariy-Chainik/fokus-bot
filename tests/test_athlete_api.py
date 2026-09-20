"""API кабинета спортсмена (/api/athlete/*): свой дневник, запись тренировки, задания, рейтинг."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_athlete_api
from bot.services.diary_service import DiaryService
from tests.fakes import AthleteTaskRepoFake, TrainingEntryRepoFake, mk_entry
from tests.test_admin_api import ADMIN_TG, YM, make_api
from tests.test_telegram_auth import make_init_data

ATHLETE_TG = 771001


def _call(dp, method, path, tg_id=ATHLETE_TG, json=None, headers=None):
    async def run():
        app = web.Application()
        register_athlete_api(app, dp)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            h = headers if headers is not None else {"Authorization": f"tma {make_init_data(user_id=tg_id)}"}
            resp = await client.request(method, path, headers=h, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.fixture()
def api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    student = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    student.athlete_tg_id = ATHLETE_TG
    dp["student_repo"].items = [student] + [s for s in dp["student_repo"].items if s.student_id != "STU-0001"]
    dp["entry_repo"] = TrainingEntryRepoFake([
        mk_entry("TE-1", "STU-0001", f"{YM}-05", 60, topics=["Джайв"], grade=4),
        mk_entry("TE-2", "STU-0001", f"{YM}-07", 45, topics=["Самба"]),
    ])
    dp["task_repo"] = AthleteTaskRepoFake()
    asyncio.run(dp["task_repo"].add("STU-0001", "TCH-0001", "Ритм в самбе", 30, "каждый день"))
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    return dp, dp


def test_only_a_linked_athlete_gets_in(api):
    app, dp = api
    status, me = _call(app, "GET", "/api/athlete/me")
    assert status == 200 and me["student"]["name"] == "Иванов Иван" and me["topics"]
    assert _call(app, "GET", "/api/athlete/me", tg_id=ADMIN_TG)[0] == 403     # админ — не спортсмен
    assert _call(app, "GET", "/api/athlete/me", headers={})[0] == 401


def test_home_shows_stats_place_and_tasks(api):
    app, dp = api
    status, h = _call(app, "GET", "/api/athlete/home")
    assert status == 200 and h["stats"]["sessions"] == 2 and h["stats"]["minutes"] == 105
    assert h["stats"]["points"] == 60 * 4 + 45 * 3           # минуты × оценка, без оценки — коэффициент 3
    assert h["place"] == 1 and h["unrated"] == 1
    assert [t["exercise"] for t in h["openTasks"]] == ["Ритм в самбе"]


def test_entry_is_created_with_topics_and_task(api):
    app, dp = api
    task_id = dp["task_repo"].items[0].task_id
    status, r = _call(app, "POST", "/api/athlete/entries", json={
        "date": f"{YM}-12", "minutes": 90, "topics": ["Румба", "Нет такой темы"],
        "taskIds": [task_id], "comment": "работали ноги"})
    assert status == 200 and r["ok"]
    entry = dp["entry_repo"].items[-1]
    assert entry.minutes == 90 and entry.topics == ["Румба"] and entry.task_ids == [task_id]

    status, t = _call(app, "GET", "/api/athlete/tasks")
    assert status == 200 and t["tasks"][0]["done"] == 1 and t["tasks"][0]["last"] == f"{YM}-12"


def test_entry_requires_date_and_minutes(api):
    app, _ = api
    assert _call(app, "POST", "/api/athlete/entries", json={"minutes": 60})[0] == 400
    assert _call(app, "POST", "/api/athlete/entries", json={"date": f"{YM}-12", "minutes": 0})[0] == 400
    assert _call(app, "POST", "/api/athlete/entries", json={"date": f"{YM}-12", "minutes": 9999})[0] == 400


def test_graded_entry_cannot_be_deleted(api):
    app, dp = api
    assert _call(app, "DELETE", "/api/athlete/entries/TE-1")[0] == 409       # есть оценка педагога
    assert _call(app, "DELETE", "/api/athlete/entries/TE-2")[0] == 200
    assert [e.entry_id for e in dp["entry_repo"].items] == ["TE-1"]


def test_entries_and_rating_of_the_month(api):
    app, _ = api
    status, d = _call(app, "GET", f"/api/athlete/entries?ym={YM}")
    assert status == 200 and [e["id"] for e in d["entries"]] == ["TE-2", "TE-1"]   # новые сверху
    assert d["entries"][0]["canDelete"] and not d["entries"][1]["canDelete"]

    status, r = _call(app, "GET", f"/api/athlete/rating?ym={YM}")
    assert status == 200 and r["me"]["place"] == 1 and r["me"]["me"] is True
    status, r = _call(app, "GET", f"/api/athlete/rating?ym={YM}&topic=Самба")
    assert status == 200 and r["topic"] == "Самба" and r["me"]["minutes"] == 45
