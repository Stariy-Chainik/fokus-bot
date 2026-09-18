"""API кабинета администратора: занятия за день, карточка, удаление с обходом замка периода."""
import pytest

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
