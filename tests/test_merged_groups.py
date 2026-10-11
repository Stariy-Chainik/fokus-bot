"""Объединённое занятие двух групп (MERGEABLE_GROUPS, решение владельца 11.10.2026: садовые группы
Яковлевой в Южной Битце). Одно занятие — одна зарплата, каждый ребёнок платит по цене своей группы,
занятие засчитывается обеим группам."""
from datetime import date
from types import SimpleNamespace as NS

import pytest

from bot.models import StudentGroup
from bot.models.enums import GroupBillingMode, LessonType
from bot.repositories.group_schedule_repo import ScheduleSlot
from bot.services.lesson_reminders import missing_for_day
from bot.utils.groups import lesson_group_title, merged_title
from config.settings import settings
from tests.fakes import mk_group, mk_lesson, mk_student, mk_teacher
from tests.test_admin_api import YM, _call, make_api

TODAY = date.today().isoformat()


@pytest.fixture()
def api(monkeypatch):
    app, dp = make_api(monkeypatch)
    # вторая садовая группа того же педагога: своя цена, чтобы видеть, по какой группе считается ребёнок
    dp["group_repo"].items.append(mk_group("GRP-0002", "БП Джаз 17:00", billing_mode=GroupBillingMode.PER_VISIT, price_full=900))
    dp["teacher_group_repo"]._t2g["TCH-0001"].append("GRP-0002")
    dp["student_repo"].items.append(mk_student("STU-0003", "Сидоров Пётр"))
    dp["student_group_repo"].rows.append(StudentGroup("STU-0003", "GRP-0002"))
    monkeypatch.setattr(settings, "mergeable_groups", "GRP-0001+GRP-0002")
    return app, dp


def test_settings_and_titles(monkeypatch):
    monkeypatch.setattr(settings, "mergeable_groups", "GRP-1+GRP-2, GRP-3+GRP-4+GRP-5,GRP-9")
    m = settings.mergeable_group_map
    assert m["GRP-1"] == {"GRP-2"} and m["GRP-4"] == {"GRP-3", "GRP-5"} and "GRP-9" not in m
    assert merged_title(["ЮБ сад ХГ 16:00–17:00 🤸", "ЮБ сад ХГ 17:00–18:00 🤸"]) == "ЮБ сад ХГ 16:00–17:00 + 17:00–18:00 🤸"
    assert merged_title(["БП Джаз"]) == "БП Джаз"
    ls = NS(group_ids=["G1", "G2"])
    assert lesson_group_title(ls, {"G1": "Сад 16", "G2": "Сад 17"}) == "Сад 16 + 17"
    assert lesson_group_title(NS(group_ids=[]), {}, "Группа") == "Группа"


def test_options_offer_merge_only_for_configured_groups(api, monkeypatch):
    app, _ = api
    groups = {g["id"]: g for g in _call(app, "GET", "/api/admin/record/options?teacher=TCH-0001")[1]["groups"]}
    assert groups["GRP-0001"]["mergeWith"] == ["GRP-0002"] and groups["GRP-0002"]["mergeWith"] == ["GRP-0001"]
    monkeypatch.setattr(settings, "mergeable_groups", "")
    groups = _call(app, "GET", "/api/admin/record/options?teacher=TCH-0001")[1]["groups"]
    assert all(g["mergeWith"] == [] for g in groups)


def test_merged_lesson_one_salary_each_child_pays_own_group(api):
    app, dp = api
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 60, "groupId": "GRP-0001",
            "mergeGroupIds": ["GRP-0002"], "studentIds": ["STU-0001", "STU-0003"]}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["created"] == 1 and r["label"] == "БП Джаз + Джаз 17:00"
    ls = dp["lesson_repo"].items[-1]
    assert ls.group_id == "GRP-0001" and ls.merged_groups == "GRP-0002" and ls.group_ids == ["GRP-0001", "GRP-0002"]
    assert ls.attendees == "STU-0001:60:800,STU-0003:60:900"          # каждый — по цене своей группы
    # зарплата — как за одно занятие 60 мин (ставка группы 1000 за 45)
    lines = [ln for ln in __import__("asyncio").run(dp["salary_service"].lines_for(
        dp["teacher_repo"].items[0], TODAY[:7])) if ln.lesson_id == ls.lesson_id]
    assert len(lines) == 1 and lines[0].amount == 1333
    # карточка и журнал — название обеих групп
    assert _call(app, "GET", f"/api/admin/lessons/{ls.lesson_id}")[1]["groupName"] == "БП Джаз + Джаз 17:00"


def test_merge_without_kids_of_second_group_is_plain_lesson(api):
    app, dp = api
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 60, "groupId": "GRP-0001",
            "mergeGroupIds": ["GRP-0002"], "studentIds": ["STU-0001"]}
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 200 and r["label"] == "БП Джаз"
    assert dp["lesson_repo"].items[-1].merged_groups == ""


def test_merge_rejected_for_not_configured_or_foreign_group(api, monkeypatch):
    app, dp = api
    body = {"teacherId": "TCH-0001", "kind": "group", "date": TODAY, "durationMin": 60, "groupId": "GRP-0001",
            "mergeGroupIds": ["GRP-0002"], "studentIds": ["STU-0001", "STU-0003"]}
    monkeypatch.setattr(settings, "mergeable_groups", "")
    status, r = _call(app, "POST", "/api/admin/record", json=body)
    assert status == 400 and r["error"] == "bad_request"
    monkeypatch.setattr(settings, "mergeable_groups", "GRP-0001+GRP-0002")
    dp["teacher_group_repo"]._t2g["TCH-0001"].remove("GRP-0002")     # группа не этого педагога
    assert _call(app, "POST", "/api/admin/record", json=body)[0] == 400


def test_reminders_count_merged_lesson_for_both_groups():
    mon = date(2026, 9, 28)
    groups = {"G1": NS(name="Сад 16", archived=False), "G2": NS(name="Сад 17", archived=False)}
    slots = [ScheduleSlot("S1", "G1", 1, "16:00", "17:00"), ScheduleSlot("S2", "G2", 1, "17:00", "18:00")]
    lessons = [mk_lesson("LES-1", mk_teacher("TCH-1", "Яковлева"), "2026-09-28", lesson_type=LessonType.GROUP,
                         group_id="G1")]
    assert [m.group_id for m in missing_for_day(mon, slots, lessons, groups, {})] == ["G2"]
    lessons[0].merged_groups = "G2"
    assert missing_for_day(mon, slots, lessons, groups, {}) == []


def test_breakdown_puts_child_into_own_group(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-9", teacher, f"{YM}-13", duration=60, lesson_type=LessonType.GROUP,
                                             attendees="STU-0001:60:800,STU-0003:60:900", group_id="GRP-0001"))
    dp["lesson_repo"].items[-1].merged_groups = "GRP-0002"
    status, d = _call(app, "GET", f"/api/admin/pay/breakdown?ym={YM}")
    assert status == 200
    by_group = {g["id"]: {s["id"] for s in g["students"]} for b in d["branches"] for g in b["groups"]}
    assert "STU-0003" in by_group["GRP-0002"] and "STU-0003" not in by_group["GRP-0001"]
