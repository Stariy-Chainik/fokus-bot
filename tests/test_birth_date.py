"""Дата рождения в карточке ученика (решение владельца 08.11.2026): колонка `students.birth_date` (19), возраст словами
и возрастная группа ФТСАРР у бальников; правят администратор и педагог своих групп, видят спортсмен и родитель."""
import asyncio
from datetime import date

import pytest

from bot.repositories.base import BaseRepository
from bot.repositories.student_repo import StudentRepository
from bot.services.birthdays import age_group, age_label, age_on, parse_birth_date, view
from bot.services.diary_service import DiaryService
from tests.fakes import AthleteTaskRepoFake, FakeSheetsClient, FakeWorksheet, TrainingEntryRepoFake, mk_group, mk_student
from tests.test_admin_api import YM, _call as admin_call, make_api
from tests.test_athlete_api import ATHLETE_TG, _call as athlete_call
from tests.test_parent_api import _call as parent_call
from tests.test_teacher_api import TEACHER_TG, _call as teacher_call

HEADERS = ["student_id", "name", "partner_id", "group_id", "group_tier", "client_id", "parent_tg_ids", "kindergarten_group",
           "athlete_tg_id", "parent_max_ids", "athlete_max_id", "archived",
           "class_st", "class_la", "class_solo_st", "class_solo_la", "sport_rank", "ftsarr_id", "birth_date"]
EMPTY = {"birthDate": "", "display": "", "age": None, "ageLabel": "", "ageGroup": ""}


def test_parse_age_and_ftsarr_age_group():
    assert parse_birth_date("04.03.2014") == parse_birth_date("2014-3-4") == parse_birth_date(" 4.3.2014 ") == "2014-03-04"
    assert parse_birth_date("") == "" and parse_birth_date(None) == ""
    assert parse_birth_date("31.02.2014") is None and parse_birth_date("2014") is None and parse_birth_date("04.03.2099") is None
    assert age_on("2014-03-04", date(2026, 3, 3)) == 11 and age_on("2014-03-04", date(2026, 3, 4)) == 12
    assert age_label(1) == "1 год" and age_label(3) == "3 года" and age_label(12) == "12 лет" and age_label(21) == "21 год"
    assert age_group("2014-03-04", 2026) == "Юниоры 1" and age_group("2020-12-31", 2026) == "Дети 0"
    assert age_group("2017-01-01", 2026) == "Дети 1" and age_group("2000-01-01", 2026) == "Взрослые"
    today = date(2026, 11, 8)
    assert view(mk_student(birth_date="2014-03-04"), True, today) == {
        "birthDate": "2014-03-04", "display": "04.03.2014", "age": 12, "ageLabel": "12 лет", "ageGroup": "Юниоры 1"}
    v = view(mk_student(birth_date="04.03.2014"), False, today)              # набрано руками в листе — тоже понимаем
    assert v["birthDate"] == "2014-03-04" and v["ageGroup"] == ""           # не бальник — без возрастной группы
    assert view(mk_student(), True, today) == EMPTY


def test_repository_writes_column_19_raw():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    raws = []

    class Sheet(FakeWorksheet):
        def batch_update(self, data, raw=True, **kw):
            raws.append(raw)
            return super().batch_update(data, raw=raw, **kw)
    ws = Sheet(HEADERS, [["STU-0001", "Иванов Иван", "", "", "full"] + [""] * 14])
    repo = StudentRepository(FakeSheetsClient(ws), "students")
    assert asyncio.run(repo.update_birth_date("STU-0001", "2014-03-04"))
    assert raws == [True] and ws.rows[0][18] == "2014-03-04"
    assert asyncio.run(repo.get_by_id("STU-0001")).birth_date == "2014-03-04"
    assert asyncio.run(repo.update_birth_date("STU-0001", "")) and ws.rows[0][18] == ""
    assert not asyncio.run(repo.update_birth_date("STU-0404", "2014-03-04"))
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()


@pytest.fixture()
def dp(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    dp["group_repo"].items.append(mk_group("GRP-0005", "БП БТ Детская спортивная"))
    asyncio.run(dp["student_group_repo"].add("STU-0001", "GRP-0005"))                # Иванов — бальник
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0001"))                # Петрова — джаз
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0005"))
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0001"))
    return dp


def test_admin_card_shows_and_edits_birth_date(dp):
    assert admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["birth"] == EMPTY
    url = "/api/admin/students/STU-0001/birth"
    status, r = admin_call(dp, "PUT", url, json={"birthDate": "04.03.2014"})
    assert status == 200 and r["ok"] and r["birthDate"] == "2014-03-04" and r["display"] == "04.03.2014"
    assert r["age"] == age_on("2014-03-04", date.today()) and r["ageLabel"] == age_label(r["age"])
    assert r["ageGroup"] == age_group("2014-03-04", date.today().year)            # бальник — возрастная группа ФТСАРР
    assert admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["birth"]["birthDate"] == "2014-03-04"
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0002/birth", json={"birthDate": "2015-05-05"})[1]["ageGroup"] == ""
    assert admin_call(dp, "PUT", url, json={"birthDate": "2014"})[0] == 400
    assert admin_call(dp, "PUT", url, json={"birthDate": "04.03.2099"})[0] == 400
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0404/birth", json={"birthDate": "04.03.2014"})[0] == 404
    assert admin_call(dp, "PUT", url, json={"birthDate": ""})[1] == {"ok": True, **EMPTY}      # стереть


def test_teacher_edits_birth_date_of_own_student(dp):
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]["birth"] == EMPTY
    status, r = teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/birth", json={"birthDate": "04.03.2014"})
    assert status == 200 and r["birthDate"] == "2014-03-04" and r["ageGroup"]
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]["birth"]["display"] == "04.03.2014"
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/birth", json={"birthDate": "x"})[0] == 400
    dp["teacher_group_repo"]._t2g["TCH-0001"] = []                                  # группы забрали — ученик не виден
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/birth", json={"birthDate": "04.03.2014"})[0] == 404


def test_athlete_and_parent_see_birth_date(dp):
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0001/birth", json={"birthDate": "04.03.2014"})[0] == 200
    student = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    student.athlete_tg_id = ATHLETE_TG
    dp["student_repo"].items = [student] + [x for x in dp["student_repo"].items if x.student_id != "STU-0001"]
    dp["entry_repo"], dp["task_repo"] = TrainingEntryRepoFake([]), AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    home = athlete_call(dp, "GET", "/api/athlete/home")[1]["birth"]
    assert home["birthDate"] == "2014-03-04" and home["ageGroup"]
    diary = parent_call(dp, "GET", f"/api/parent/diary/STU-0001?ym={YM}")[1]["birth"]
    assert diary["display"] == "04.03.2014" and diary["age"] == home["age"]
