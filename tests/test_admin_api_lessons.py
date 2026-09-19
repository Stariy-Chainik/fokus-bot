"""API кабинета администратора: занятия за день, карточка, удаление с обходом замка периода."""
import pytest

from bot.models.enums import GroupBillingMode, LessonType
from tests.fakes import mk_lesson
from tests.test_admin_api import YM, _call, make_api


@pytest.fixture()
def api(monkeypatch):
    return make_api(monkeypatch)


def test_lessons_of_day_and_detail(api):
    app, _ = api
    status, d = _call(app, "GET", f"/api/admin/lessons?date={YM}-12")
    assert status == 200 and len(d["lessons"]) == 1
    ls = d["lessons"][0]
    assert (ls["teacherName"], ls["type"], ls["groupName"]) == ("Река Станислав", "group", "БП Джаз")
    assert ls["students"] == ["Иванов Иван", "Петрова Анна"]
    assert ls["earned"] == 1333 and ls["locked"] is False and d["earned"] == 1333
    assert _call(app, "GET", "/api/admin/lessons?date=2026-01-01")[1]["lessons"] == []
    assert _call(app, "GET", "/api/admin/lessons?date=bad")[0] == 400

    status, one = _call(app, "GET", "/api/admin/lessons/LES-3")
    assert status == 200 and [(a["name"], a["amount"]) for a in one["attendees"]] == [("Иванов Иван", 800), ("Петрова Анна", 800)]
    status, ind = _call(app, "GET", "/api/admin/lessons/LES-1")
    assert status == 200 and ind["attendees"] == [{"studentId": "STU-0001", "name": "Иванов Иван", "durationMin": 45, "amount": None}]
    assert _call(app, "GET", "/api/admin/lessons/LES-404")[0] == 404


def test_delete_lesson_bypasses_period_lock(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-OLD", teacher, "2026-08-20", students=[("STU-0002", "Петрова Анна")]))
    status, d = _call(app, "GET", "/api/admin/lessons?date=2026-08-20")
    assert d["lessons"][0]["locked"] is True                      # август сдан педагогом
    assert _call(app, "DELETE", "/api/admin/lessons/LES-OLD")[0] == 200
    assert _call(app, "GET", "/api/admin/lessons?date=2026-08-20")[1]["lessons"] == []
    assert _call(app, "DELETE", "/api/admin/lessons/LES-OLD")[0] == 404


def test_lesson_card_says_why_a_visit_costs_nothing(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    # Группа «по посещению»: ноль у пришедшего — пробное занятие, а не абонемент.
    dp["lesson_repo"].items.append(mk_lesson("LES-FREE", teacher, f"{YM}-14", duration=60, lesson_type=LessonType.GROUP,
                                             attendees="STU-0001:60:800,STU-0002:60:0", group_id="GRP-0001"))
    status, d = _call(app, "GET", "/api/admin/lessons/LES-FREE")
    assert status == 200 and d["groupMode"] == "per_visit" and d["freeLabel"] == "пробное"
    assert [(a["name"], a["amount"]) for a in d["attendees"]] == [("Иванов Иван", 800), ("Петрова Анна", 0)]

    dp["group_repo"].items[0].billing_mode = GroupBillingMode.SUBSCRIPTION
    assert _call(app, "GET", "/api/admin/lessons/LES-FREE")[1]["freeLabel"] == "абонемент"
