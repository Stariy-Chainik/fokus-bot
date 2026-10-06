"""«Кто был» в сохранённом занятии (решение владельца 07.10.2026): журнальные группы правятся целиком,
в «по посещению» новым — цена группы, уже начисленного не снять; педагог — только своё и не в сданном периоде."""
import asyncio

import pytest

from bot.models.enums import GroupBillingMode, LessonType
from bot.services.lesson_service import LessonService
from config.settings import settings
from tests.fakes import (
    LessonRepoFake, SubmissionRepoFake, TeacherRepoFake, mk_group, mk_lesson, mk_student, mk_submission, mk_teacher,
)
from tests.test_admin_api import _call as admin_call, make_api
from tests.test_teacher_api import TEACHER_TG, _call as teacher_call

T = mk_teacher("TCH-0001")
STU = {sid: mk_student(sid, n) for sid, n in (("STU-1", "Аня"), ("STU-2", "Боря"), ("STU-3", "Вера"), ("STU-4", "Гоша"))}
JOURNAL = mk_group("GRP-0029", "Современные", billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=5500)
VISIT = mk_group("GRP-0001", "БП Джаз", billing_mode=GroupBillingMode.PER_VISIT, price_full=800)


def _svc(lesson, submitted=()):
    repo = LessonRepoFake([lesson])
    return LessonService(repo, SubmissionRepoFake([mk_submission("TCH-0001", p) for p in submitted]), TeacherRepoFake([T])), repo


def _lesson(group, attendees=None):
    return mk_lesson("LES-9", T, "2026-10-01", 60, LessonType.GROUP, group_id=group.group_id, attendees=attendees)


def test_journal_group_adds_and_removes_freely(monkeypatch):
    monkeypatch.setattr(settings, "attendance_group_ids", "GRP-0029")
    ls = _lesson(JOURNAL)                                        # сохранено без отметки, как у Фоминой 01.10
    svc, repo = _svc(ls)
    r = asyncio.run(svc.set_attendance(ls, JOURNAL, ["STU-2", "STU-1", "STU-1", "STU-404"], STU))
    assert (r["added"], r["removed"], ls.attendees) == (["STU-2", "STU-1"], [], "STU-2,STU-1")   # старый CSV — без сумм
    r = asyncio.run(svc.set_attendance(ls, JOURNAL, ["STU-1"], STU))
    assert (r["added"], r["removed"], ls.attendees) == ([], ["STU-2"], "STU-1")
    before = len(repo.attendees_updates)
    asyncio.run(svc.set_attendance(ls, JOURNAL, ["STU-1"], STU))     # без изменений — лист не трогаем
    assert len(repo.attendees_updates) == before


def test_per_visit_adds_at_group_price_and_keeps_charged(monkeypatch):
    ls = _lesson(VISIT, "STU-1:60:800,STU-2:60:0")               # Аня начислена, Боря — пробное
    svc, _ = _svc(ls)
    r = asyncio.run(svc.set_attendance(ls, VISIT, ["STU-3"], STU))
    assert r["removed"] == ["STU-2"] and r["kept"] == ["STU-1"] and r["added"] == ["STU-3"]
    assert ls.attendees == "STU-1:60:800,STU-3:60:800"


def test_untracked_group_and_locked_period(monkeypatch):
    monkeypatch.setattr(settings, "attendance_group_ids", "")
    ls = _lesson(JOURNAL)
    svc, _ = _svc(ls)
    with pytest.raises(ValueError, match="not_tracked"):
        asyncio.run(svc.set_attendance(ls, JOURNAL, ["STU-1"], STU))
    monkeypatch.setattr(settings, "teacher_period_submit_enabled", True)
    ls = _lesson(VISIT)
    svc, _ = _svc(ls, submitted=["2026-10"])
    with pytest.raises(PermissionError):
        asyncio.run(svc.set_attendance(ls, VISIT, ["STU-1"], STU))
    assert asyncio.run(svc.set_attendance(ls, VISIT, ["STU-1"], STU, bypass_period_lock=True))["added"] == ["STU-1"]


def test_revenue_share_group_allows_three(monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0001:50")
    ls = _lesson(VISIT)
    svc, _ = _svc(ls)
    with pytest.raises(ValueError, match="too_many"):
        asyncio.run(svc.set_attendance(ls, VISIT, list(STU), STU))


@pytest.fixture()
def api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    return dp


def test_teacher_and_admin_api(api, monkeypatch):
    """Карточка занятия показывает кнопку, экран «Кто был» — состав группы месяца; педагог правит только своё,
    в сданном периоде — 409, администратор — всегда."""
    dp = api
    # LES-3 — группа «по посещению» GRP-0001: оба ученика начислены по 800
    status, l3 = teacher_call(dp, "GET", "/api/teacher/lessons/LES-3")
    assert status == 200 and l3["canEditAttendance"] is True
    assert teacher_call(dp, "GET", "/api/teacher/lessons/LES-1")[1]["canEditAttendance"] is False   # индивидуальное
    status, att = teacher_call(dp, "GET", "/api/teacher/lessons/LES-3/attendance")
    assert status == 200 and att["perVisit"] and att["price"] == 800
    assert [(s["name"], s["checked"], s["fixed"]) for s in att["students"]] == [
        ("Иванов Иван", True, True), ("Петрова Анна", True, True)]
    status, r = teacher_call(dp, "PUT", "/api/teacher/lessons/LES-3/attendance", json={"studentIds": []})
    assert status == 200 and (r["added"], r["removed"], r["kept"]) == (0, 0, 2)    # начисленных не снять

    # журнальная группа: занятие без отметки → отметить двоих
    monkeypatch.setattr(settings, "attendance_group_ids", "GRP-0002")
    dp["group_repo"].items.append(mk_group("GRP-0002", "Современные", billing_mode=GroupBillingMode.SUBSCRIPTION,
                                           price_full=5500))
    asyncio.run(dp["student_group_repo"].add("STU-0001", "GRP-0002"))
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0002"))
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    dp["lesson_repo"].items.append(mk_lesson("LES-7", teacher, f"{l3['date'][:7]}-01", 60, LessonType.GROUP,
                                             group_id="GRP-0002"))
    att = teacher_call(dp, "GET", "/api/teacher/lessons/LES-7/attendance")[1]
    assert [(s["id"], s["checked"], s["member"]) for s in att["students"]] == [
        ("STU-0001", False, True), ("STU-0002", False, True)]
    asyncio.run(dp["lesson_repo"].update_attendees("LES-7", "STU-0002"))           # отмеченные — сверху списка
    assert [s["id"] for s in teacher_call(dp, "GET", "/api/teacher/lessons/LES-7/attendance")[1]["students"]] == [
        "STU-0002", "STU-0001"]
    asyncio.run(dp["lesson_repo"].update_attendees("LES-7", ""))
    status, r = teacher_call(dp, "PUT", "/api/teacher/lessons/LES-7/attendance",
                             json={"studentIds": ["STU-0001", "STU-0002"]})
    assert status == 200 and r["added"] == 2
    assert asyncio.run(dp["lesson_repo"].get_by_id("LES-7")).attendees == "STU-0001,STU-0002"
    assert teacher_call(dp, "PUT", "/api/teacher/lessons/LES-7/attendance", json={"studentIds": "x"})[0] == 400

    # чужое занятие — 404; сданный период — 409 педагогу, администратор правит
    other = mk_teacher("TCH-0002", "Другой")
    dp["lesson_repo"].items.append(mk_lesson("LES-8", other, f"{l3['date'][:7]}-02", 60, LessonType.GROUP,
                                             group_id="GRP-0002"))
    assert teacher_call(dp, "GET", "/api/teacher/lessons/LES-8/attendance")[0] == 404
    monkeypatch.setattr(settings, "teacher_period_submit_enabled", True)
    dp["submission_repo"].items.append(mk_submission("TCH-0001", l3["date"][:7]))
    status, err = teacher_call(dp, "PUT", "/api/teacher/lessons/LES-7/attendance", json={"studentIds": ["STU-0001"]})
    assert status == 409 and err["error"] == "period_locked"
    assert teacher_call(dp, "GET", "/api/teacher/lessons/LES-7")[1]["canEditAttendance"] is False
    status, a7 = admin_call(dp, "GET", "/api/admin/lessons/LES-7")
    assert status == 200 and a7["canEditAttendance"] is True
    status, r = admin_call(dp, "PUT", "/api/admin/lessons/LES-7/attendance", json={"studentIds": ["STU-0001"]})
    assert status == 200 and r["removed"] == 1
    assert admin_call(dp, "GET", "/api/admin/lessons/LES-404/attendance")[0] == 404



def test_revenue_share_lesson_offers_students_of_teacher_groups(api, monkeypatch):
    """Техгруппа «Индивидуальные — …» своего состава не имеет: в «Кто был» — ученики групп педагога."""
    dp = api
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    dp["group_repo"].items.append(mk_group("GRP-0020", "ХГ Индивидуальные", billing_mode=GroupBillingMode.PER_VISIT,
                                           price_full=1800))
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0020"))
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    les3 = asyncio.run(dp["lesson_repo"].get_by_id("LES-3"))
    dp["lesson_repo"].items.append(mk_lesson("LES-20", teacher, les3.date, 60, LessonType.GROUP, group_id="GRP-0020",
                                             attendees="STU-0001:60:1800"))
    att = teacher_call(dp, "GET", "/api/teacher/lessons/LES-20/attendance")[1]
    assert att["max"] == 3 and [(s["id"], s["checked"], s["member"]) for s in att["students"]] == [
        ("STU-0001", True, True), ("STU-0002", False, True)]
    status, r = teacher_call(dp, "PUT", "/api/teacher/lessons/LES-20/attendance",
                             json={"studentIds": ["STU-0001", "STU-0002"]})
    assert status == 200 and r["added"] == 1
    assert asyncio.run(dp["lesson_repo"].get_by_id("LES-20")).attendees == "STU-0001:60:1800,STU-0002:60:1800"
