"""API кабинета администратора: запись занятия за педагога (мастер как в боте)."""
from datetime import date, timedelta

import pytest

from bot.models.enums import GroupBillingMode, StudentGroupTier
from tests.fakes import mk_group, mk_student
from tests.test_admin_api import _call, make_api

TODAY = date.today().isoformat()


@pytest.fixture()
def api(monkeypatch):
    return make_api(monkeypatch)


def test_record_options(api):
    app, dp = api
    dp["student_repo"].items[0].partner_id = "STU-0002"; dp["student_repo"].items[1].partner_id = "STU-0001"  # noqa: E702
    status, o = _call(app, "GET", "/api/admin/record/options?teacher=TCH-0001")
    assert status == 200 and o["kinds"] == ["group", "pair", "shared", "soloist"] and o["durations"][0] == 30
    g = o["groups"][0]
    assert (g["name"], g["mode"], g["priceFull"]) == ("БП Джаз", "per_visit", 800)
    assert [(r["name"], r["tier"]) for r in g["roster"]] == [("Иванов Иван", "full"), ("Петрова Анна", "full")]
    assert o["pairs"] == [{"aId": "STU-0001", "aName": "Иванов Иван", "bId": "STU-0002", "bName": "Петрова Анна"}]
    assert [s["name"] for s in o["students"]] == ["Иванов Иван", "Петрова Анна"]
    assert _call(app, "GET", "/api/admin/record/options?teacher=TCH-0404")[0] == 404


def test_record_group_with_tiers_and_none_mode(api):
    app, dp = api
    dp["student_repo"].items[1].group_tier = StudentGroupTier.SHORT
    dp["group_repo"].items[0].price_short = 500
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 60, "groupId": "GRP-0001",
            "studentIds": ["STU-0001", "STU-0002"], "tiers": {"STU-0001": "short"}}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["created"] == 1 and r["attendees"] == 2
    ls = dp["lesson_repo"].items[-1]
    assert ls.group_id == "GRP-0001" and ls.attendees == "STU-0001:35:500,STU-0002:35:500"   # разовый тариф + тариф карточки

    dp["group_repo"].items.append(mk_group("GRP-0002", "БП Хореография", billing_mode=GroupBillingMode.NONE))
    dp["teacher_group_repo"]._t2g["TCH-0001"].append("GRP-0002")
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 45, "groupId": "GRP-0002", "studentIds": []}
    assert _call(app, "POST", "/api/admin/record", json=body)[0] == 200
    assert dp["lesson_repo"].items[-1].attendees is None


def test_record_pair_shared_soloist_and_errors(api):
    app, dp = api
    dp["student_repo"].items[0].partner_id = "STU-0002"; dp["student_repo"].items[1].partner_id = "STU-0001"  # noqa: E702
    dp["student_repo"].items.append(mk_student("STU-0003", "Сидоров Пётр"))
    dp["student_group_repo"].rows.append(__import__("bot.models", fromlist=["StudentGroup"]).StudentGroup("STU-0003", "GRP-0001"))

    body = {"teacherId": "TCH-0001", "kind": "pair", "date": TODAY, "durationMin": 45, "studentIds": ["STU-0001"]}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["created"] == 1 and r["label"] == "Иванов Иван ↔ Петрова Анна"
    ls = dp["lesson_repo"].items[-1]
    assert (ls.student_1_id, ls.student_2_id, ls.type.value) == ("STU-0001", "STU-0002", "individual")

    body = {"teacherId": "TCH-0001", "kind": "shared", "date": TODAY, "durationMin": 60, "studentIds": ["STU-0003", "STU-0001"]}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["label"] == "Иванов Иван + Сидоров Пётр"
    assert _call(app, "POST", "/api/admin/record", json={**body, "studentIds": ["STU-0001"]})[0] == 400

    body = {"teacherId": "TCH-0001", "kind": "soloist", "date": TODAY, "durationMin": 45, "studentIds": ["STU-0003", "STU-0002"]}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["created"] == 2
    status, r = _call(app, "POST", "/api/admin/record", json={**body, "studentIds": ["STU-0003"]})   # дубль соло в тот же день
    assert status == 409 and r["error"] == "conflict"

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert _call(app, "POST", "/api/admin/record", json={**body, "date": tomorrow})[1]["error"] == "future_date"
    assert _call(app, "POST", "/api/admin/record", json={**body, "teacherId": "TCH-0404"})[0] == 404
    # сданный период: у админа обходится
    body = {"teacherId": "TCH-0001", "kind": "soloist", "date": "2026-08-15", "durationMin": 45, "studentIds": ["STU-0003"]}
    assert _call(app, "POST", "/api/admin/record", json=body)[0] == 200


def test_record_group_marks_trial_visit_free(api):
    app, dp = api
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 60, "groupId": "GRP-0001",
            "studentIds": ["STU-0001", "STU-0002"], "tiers": {"STU-0002": "trial"}}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["attendees"] == 2
    assert dp["lesson_repo"].items[-1].attendees == "STU-0001:60:800,STU-0002:60:0"
