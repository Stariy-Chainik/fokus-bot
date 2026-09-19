"""API кабинета педагога (/api/teacher/*): роль, свои занятия, группы, статистика, сдача периода."""
import asyncio
from datetime import date

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_teacher_api
from bot.models.enums import LessonType
from tests.fakes import mk_lesson, mk_submission, mk_user
from tests.test_admin_api import ADMIN_TG, PARENT_TG, YM, make_api
from tests.test_telegram_auth import make_init_data

TEACHER_TG = 4242


def _call(dp, method, path, tg_id=TEACHER_TG, json=None, headers=None):
    async def run():
        app = web.Application()
        register_teacher_api(app, dp)
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
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))   # педагог со своим кабинетом
    return dp, dp


def test_only_linked_teacher_gets_in(api):
    app, dp = api
    status, me = _call(app, "GET", "/api/teacher/me")
    assert status == 200 and (me["teacherId"], me["name"]) == ("TCH-0001", "Река Станислав")
    assert _call(app, "GET", "/api/teacher/me", tg_id=PARENT_TG)[0] == 403        # родитель — не педагог
    assert _call(app, "GET", "/api/teacher/me", tg_id=ADMIN_TG)[0] == 403         # админ без teacher_id
    assert _call(app, "GET", "/api/teacher/me", headers={})[0] == 401


def test_home_and_lessons_are_own_only(api):
    app, dp = api
    status, h = _call(app, "GET", "/api/teacher/home")
    assert status == 200 and h["lessonsMonth"] == 3 and h["earnedMonth"] == 4333
    assert h["prevSubmitted"] is True and h["periodSubmitted"] is False        # август сдан, текущий — нет

    status, d = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")
    assert status == 200 and [x["id"] for x in d["lessons"]] == ["LES-1", "LES-2", "LES-3"]
    assert d["lessons"][2]["students"] == ["Иванов Иван", "Петрова Анна"] and d["lessons"][2]["groupName"] == "БП Джаз"
    assert d["earned"] == 4333 and all(x["locked"] is False for x in d["lessons"])

    status, one = _call(app, "GET", "/api/teacher/lessons/LES-3")
    assert status == 200 and one["earned"] == 1333 and [a["amount"] for a in one["attendees"]] == [800, 800]
    dp["lesson_repo"].items.append(mk_lesson("LES-OTHER", dp["teacher_repo"].items[0], f"{YM}-05"))
    dp["lesson_repo"].items[-1].teacher_id = "TCH-0099"                        # чужое занятие не видно
    assert _call(app, "GET", "/api/teacher/lessons/LES-OTHER")[0] == 404
    assert _call(app, "DELETE", "/api/teacher/lessons/LES-OTHER")[0] == 404


def test_period_lock_blocks_delete_and_record(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-AUG", teacher, "2026-08-20", students=[("STU-0001", "Иванов Иван")]))
    status, err = _call(app, "DELETE", "/api/teacher/lessons/LES-AUG")         # август сдан
    assert status == 409 and err["error"] == "period_locked"
    body = {"kind": "soloist", "date": "2026-08-21", "durationMin": 45, "studentIds": ["STU-0001"]}
    assert _call(app, "POST", "/api/teacher/record", json=body)[0] == 409
    # свой открытый месяц: запись и удаление работают
    status, r = _call(app, "POST", "/api/teacher/record",
                      json={"kind": "soloist", "date": f"{YM}-14", "durationMin": 45, "studentIds": ["STU-0002"]})
    assert status == 200 and r["created"] == 1
    assert _call(app, "DELETE", f"/api/teacher/lessons/{r['lessons'][0]}")[1]["ok"] is True


def test_record_options_and_groups_are_scoped_to_teacher(api):
    app, dp = api
    status, o = _call(app, "GET", "/api/teacher/record/options")
    assert status == 200 and o["teacherId"] == "TCH-0001" and [g["id"] for g in o["groups"]] == ["GRP-0001"]

    status, g = _call(app, "GET", "/api/teacher/groups")
    assert status == 200 and [(x["name"], x["students"]) for x in g["groups"]] == [("БП Джаз", 2)]
    status, card = _call(app, "GET", "/api/teacher/groups/GRP-0001")
    assert status == 200 and [s["name"] for s in card["students"]] == ["Иванов Иван", "Петрова Анна"]
    assert _call(app, "GET", "/api/teacher/groups/GRP-0404")[0] == 404

    status, s = _call(app, "GET", f"/api/teacher/students/STU-0001?ym={YM}")
    assert status == 200 and s["name"] == "Иванов Иван" and [x["id"] for x in s["lessons"]] == ["LES-1", "LES-2", "LES-3"]


def test_stats_and_submit_period(api):
    app, dp = api
    status, st = _call(app, "GET", f"/api/teacher/stats?ym={YM}")
    assert status == 200 and st["total"] == 4333 and (st["groupLessons"], st["individualLessons"]) == (1, 2)
    # у строк-занятий свой label пустой — подставляем, кто занимался
    assert [ln["label"] for ln in st["lines"]] == ["Иванов Иван", "Иванов Иван", "БП Джаз"]

    status, pre = _call(app, "GET", f"/api/teacher/submit?ym={YM}")
    assert status == 200 and pre["lessons"] == 3 and pre["submitted"] is False
    if date.today().day < 25:                                   # правило «сдать с 25-го»
        assert pre["canSubmit"] is False
        assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[0] == 409
        return
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[1]["ok"] is True
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[0] == 409     # повторно — нельзя


def test_submit_rejects_already_submitted_month(api):
    app, dp = api
    dp["submission_repo"].items.append(mk_submission("TCH-0001", "2026-07"))
    status, err = _call(app, "POST", "/api/teacher/submit", json={"ym": "2026-07"})
    assert status == 409 and err["error"] == "already"
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": "bad"})[0] == 400


def test_lesson_types_are_reported(api):
    app, dp = api
    status, d = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")
    kinds = {x["id"]: x["type"] for x in d["lessons"]}
    assert kinds == {"LES-1": LessonType.INDIVIDUAL.value, "LES-2": LessonType.INDIVIDUAL.value,
                     "LES-3": LessonType.GROUP.value}


def test_unknown_user_is_forbidden(api):
    app, dp = api
    assert _call(app, "GET", "/api/teacher/home", tg_id=999999)[0] == 403
    dp["user_repo"].items.append(mk_user(777, teacher_id="TCH-0404"))    # ссылка на несуществующего педагога
    assert _call(app, "GET", "/api/teacher/home", tg_id=777)[0] == 403
