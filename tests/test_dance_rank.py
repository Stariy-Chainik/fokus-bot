"""Танцевальный класс и спортивный разряд бальника (решение владельца 08.11.2026): поля `students.dance_class` /
`sport_rank` только у учеников групп бальных танцев; правят администратор и педагог своих групп, видят спортсмен
в дневнике и родитель в дневнике ребёнка; значения — из фиксированных списков."""
import asyncio

import pytest

from bot.repositories.base import BaseRepository
from bot.repositories.student_repo import StudentRepository
from bot.services.dance_rank import DANCE_CLASSES, SPORT_RANKS, clean_class, clean_rank, is_ballroom_group
from bot.services.diary_service import DiaryService
from tests.fakes import AthleteTaskRepoFake, FakeSheetsClient, FakeWorksheet, TrainingEntryRepoFake, mk_group
from tests.test_admin_api import YM, _call as admin_call, make_api
from tests.test_athlete_api import ATHLETE_TG, _call as athlete_call
from tests.test_parent_api import _call as parent_call
from tests.test_teacher_api import TEACHER_TG, _call as teacher_call

HEADERS = ["student_id", "name", "partner_id", "group_id", "group_tier", "client_id", "parent_tg_ids",
           "kindergarten_group", "athlete_tg_id", "parent_max_ids", "athlete_max_id", "archived", "dance_class", "sport_rank"]


def test_ballroom_detection_and_values():
    assert is_ballroom_group(mk_group(name="БП БТ Спортивная — Никишин")) and is_ballroom_group(mk_group(name="Бальные танцы взрослые"))
    assert not is_ballroom_group(mk_group(name="ВБ ХГ Старшая")) and not is_ballroom_group(mk_group(name="БП Джаз"))
    assert not is_ballroom_group(None)
    assert clean_class("e") == "E" and clean_class("Е") == "E" and clean_class(" н-3 ") == "Н-3" and clean_class("H-6") == "Н-6"
    assert clean_class("") == "" and clean_class(None) == "" and clean_class("X") is None and clean_class("Н-7") is None
    assert clean_rank("кмс") == "КМС" and clean_rank("1 юношеский") == "1 юношеский" and clean_rank("мастер") is None
    assert len(DANCE_CLASSES) == 12 and len(SPORT_RANKS) == 10


def test_repository_writes_both_columns_raw():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    raws = []

    class Sheet(FakeWorksheet):
        def batch_update(self, data, raw=True, **kw):
            raws.append(raw)
            return super().batch_update(data, raw=raw, **kw)
    ws = Sheet(HEADERS, [["STU-0001", "Иванов Иван", "", "", "full", "", "", "", "", "", "", "", "", ""]])
    repo = StudentRepository(FakeSheetsClient(ws), "students")
    assert asyncio.run(repo.update_dance_profile("STU-0001", dance_class="E", sport_rank="1 юношеский"))
    assert raws == [True] and ws.rows[0][12:14] == ["E", "1 юношеский"]
    s = asyncio.run(repo.get_by_id("STU-0001"))
    assert (s.dance_class, s.sport_rank) == ("E", "1 юношеский")
    assert asyncio.run(repo.update_dance_profile("STU-0001", sport_rank="КМС"))          # класс не трогаем
    assert ws.rows[0][12:14] == ["E", "КМС"]
    assert asyncio.run(repo.update_dance_profile("STU-0001"))                            # нечего писать — ок
    assert not asyncio.run(repo.update_dance_profile("STU-0404", dance_class="E"))
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


def test_admin_card_shows_and_edits_class_and_rank(dp):
    card = admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["dance"]
    assert (card["ballroom"], card["danceClass"], card["sportRank"]) == (True, "", "")
    assert card["classes"] == DANCE_CLASSES and card["ranks"] == SPORT_RANKS
    assert admin_call(dp, "GET", "/api/admin/students/STU-0002")[1]["dance"]["ballroom"] is False   # джаз — блока нет
    url = "/api/admin/students/STU-0001/dance"
    assert admin_call(dp, "PUT", url, json={"danceClass": "E", "sportRank": "1 юношеский"}) == \
        (200, {"ok": True, "danceClass": "E", "sportRank": "1 юношеский"})
    card = admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["dance"]
    assert (card["danceClass"], card["sportRank"]) == ("E", "1 юношеский")
    assert admin_call(dp, "PUT", url, json={"sportRank": "КМС"})[1] == {"ok": True, "danceClass": "E", "sportRank": "КМС"}
    assert admin_call(dp, "PUT", url, json={"danceClass": "Z"})[0] == 400                         # не из списка
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0002/dance", json={"danceClass": "E"})[0] == 400   # не бальник
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0404/dance", json={"danceClass": "E"})[0] == 404
    # ушёл из бальной группы с присвоенным классом — блок остаётся, можно стереть
    dp["student_group_repo"].rows = [r for r in dp["student_group_repo"].rows if r.group_id != "GRP-0005"]
    assert admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["dance"]["ballroom"] is True
    assert admin_call(dp, "PUT", url, json={"danceClass": "", "sportRank": ""})[1]["danceClass"] == ""
    assert admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]["dance"]["ballroom"] is False


def test_teacher_sees_and_edits_own_ballroom_student(dp):
    card = teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]["dance"]
    assert card["ballroom"] is True and card["danceClass"] == ""
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0002")[1]["dance"]["ballroom"] is False
    r = teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/dance", json={"danceClass": "D", "sportRank": "2 юношеский"})
    assert r == (200, {"ok": True, "danceClass": "D", "sportRank": "2 юношеский"})
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]["dance"]["danceClass"] == "D"
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0002/dance", json={"danceClass": "D"})[0] == 400
    dp["teacher_group_repo"]._t2g["TCH-0001"] = []                                  # группы забрали — ученик не виден
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/dance", json={"danceClass": "C"})[0] == 404   # не виден


def test_athlete_and_parent_see_class_and_rank(dp):
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0001/dance", json={"danceClass": "E", "sportRank": "1 юношеский"})[0] == 200
    student = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    student.athlete_tg_id = ATHLETE_TG
    dp["student_repo"].items = [student] + [x for x in dp["student_repo"].items if x.student_id != "STU-0001"]
    dp["entry_repo"], dp["task_repo"] = TrainingEntryRepoFake([]), AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    home = athlete_call(dp, "GET", "/api/athlete/home")[1]
    assert (home["dance"]["ballroom"], home["dance"]["danceClass"], home["dance"]["sportRank"]) == (True, "E", "1 юношеский")
    diary = parent_call(dp, "GET", f"/api/parent/diary/STU-0001?ym={YM}")[1]
    assert (diary["dance"]["danceClass"], diary["dance"]["sportRank"]) == ("E", "1 юношеский")
