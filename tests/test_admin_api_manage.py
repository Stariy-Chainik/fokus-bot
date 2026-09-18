"""API кабинета администратора, этап 3: филиалы/группы, биллинг, состав, карточки учеников и педагогов."""
import pytest

from bot.models.enums import GroupBillingMode, StudentGroupTier
from tests.fakes import mk_group, mk_lesson
from tests.test_admin_api import ADMIN_TG, YM, _call, make_api


@pytest.fixture()
def api(monkeypatch):
    return make_api(monkeypatch)


def test_branches_and_groups_crud(api):
    app, dp = api
    status, b = _call(app, "GET", "/api/admin/branches")
    assert status == 200 and b["branches"][0]["groups"][0]["name"] == "БП Джаз"
    assert b["branches"][0]["groups"][0]["students"] == 2 and b["branches"][0]["groups"][0]["teachers"] == ["Река Станислав"]

    assert _call(app, "DELETE", "/api/admin/branches/BRN-0001")[0] == 409           # есть группы
    status, nb = _call(app, "POST", "/api/admin/branches", json={"name": "Боброво"})
    assert status == 200 and nb["id"] == "BRN-0002"
    assert _call(app, "PATCH", "/api/admin/branches/BRN-0002", json={"name": "Боброво 2"})[0] == 200
    status, ng = _call(app, "POST", "/api/admin/groups", json={"branchId": "BRN-0002", "name": "ХГ Младшая"})
    assert status == 200 and ng["id"] == "GRP-0002"
    assert _call(app, "POST", "/api/admin/groups", json={"branchId": "BRN-0404", "name": "x"})[0] == 400
    assert _call(app, "PATCH", "/api/admin/groups/GRP-0002", json={"name": "ХГ Младшая А", "archived": True})[0] == 200
    g = _call(app, "GET", "/api/admin/groups/GRP-0002")[1]
    assert (g["name"], g["archived"], g["branchName"]) == ("ХГ Младшая А", True, "Боброво 2")
    assert _call(app, "DELETE", "/api/admin/groups/GRP-0002")[0] == 200
    assert _call(app, "DELETE", "/api/admin/branches/BRN-0002")[0] == 200
    assert _call(app, "GET", "/api/admin/groups/GRP-0002")[0] == 404


def test_group_card_teachers_members_periods(api):
    app, dp = api
    status, g = _call(app, "GET", "/api/admin/groups/GRP-0001")
    assert status == 200 and [t["assigned"] for t in g["teachers"]] == [True]
    assert [(m["name"], m["joined"], m["left"]) for m in g["members"]] == [("Иванов Иван", "", ""), ("Петрова Анна", "", "")]

    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/teachers", json={"teacherId": "TCH-0001", "assigned": False})[0] == 200
    assert _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["teachers"][0]["assigned"] is False
    assert _call(app, "PUT", "/api/admin/teachers/TCH-0001/groups", json={"groupId": "GRP-0001", "assigned": True})[0] == 200
    assert _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["teachers"][0]["assigned"] is True

    # выход из группы «по посещению» — строка удаляется; из абонементной — помечается месяц ухода
    assert _call(app, "DELETE", "/api/admin/groups/GRP-0001/members/STU-0002")[1]["result"] == "removed"
    assert [m["name"] for m in _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["members"]] == ["Иванов Иван"]
    assert _call(app, "POST", "/api/admin/groups/GRP-0001/members", json={"studentId": "STU-0002"})[0] == 200
    dp["group_repo"].items[0].billing_mode = GroupBillingMode.SUBSCRIPTION
    assert _call(app, "DELETE", "/api/admin/groups/GRP-0001/members/STU-0002", json={"leftPeriod": "2026-10"})[1]["result"] == "marked"
    m = next(x for x in _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["members"] if x["id"] == "STU-0002")
    assert m["left"] == "2026-10"
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/members/STU-0002", json={"joinedPeriod": "2026-05", "leftPeriod": ""})[0] == 200
    m = next(x for x in _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["members"] if x["id"] == "STU-0002")
    assert (m["joined"], m["left"]) == ("2026-05", "")
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/members/STU-0002", json={"joinedPeriod": "bad"})[0] == 400


def test_group_billing_and_overrides(api):
    app, dp = api
    dp["lesson_repo"].items.append(mk_lesson("LES-OLD", dp["teacher_repo"].items[0], "2026-08-05", duration=60,
                                             lesson_type=__import__("bot.models.enums", fromlist=["LessonType"]).LessonType.GROUP,
                                             attendees=None, group_id="GRP-0001"))
    body = {"mode": "subscription", "priceFull": 7000, "effectivePeriod": YM}
    status, r = _call(app, "PUT", "/api/admin/groups/GRP-0001/billing", json=body)
    assert status == 200 and r["pinned"] == 1                                  # август зафиксирован нулём (первое включение)
    g = _call(app, "GET", "/api/admin/groups/GRP-0001")[1]
    assert (g["mode"], g["priceFull"]) == ("subscription", 7000)
    assert g["overrides"] == [{"periodMonth": "2026-08", "studentId": None, "studentName": None, "amount": 0}]
    assert _call(app, "GET", f"/api/admin/bill/STU-0001?ym={YM}")[1]["total"] == 4800 + 7000

    ovr = {"periodMonth": "*", "studentId": "STU-0002", "amount": 0}
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/overrides", json=ovr)[0] == 200
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/overrides", json={**ovr, "studentId": None})[0] == 400
    assert _call(app, "GET", f"/api/admin/bill/STU-0002?ym={YM}")[1]["total"] == 800    # абонемента нет
    assert _call(app, "DELETE", "/api/admin/groups/GRP-0001/overrides", json={"periodMonth": "*", "studentId": "STU-0002"})[0] == 200
    assert _call(app, "GET", f"/api/admin/bill/STU-0002?ym={YM}")[1]["total"] == 7800

    per_visit = {"mode": "per_visit", "priceFull": 900, "priceShort": 500}
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/billing", json=per_visit)[0] == 200
    g = _call(app, "GET", "/api/admin/groups/GRP-0001")[1]
    assert (g["mode"], g["priceFull"], g["priceShort"]) == ("per_visit", 900, 500)
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/billing", json={"mode": "none"})[0] == 200
    assert _call(app, "GET", "/api/admin/groups/GRP-0001")[1]["mode"] == "none"
    zero = {"mode": "subscription", "priceFull": 0, "effectivePeriod": YM}
    assert _call(app, "PUT", "/api/admin/groups/GRP-0001/billing", json=zero)[0] == 400


def test_student_crud_partner_groups_tier_client(api):
    app, dp = api
    status, r = _call(app, "POST", "/api/admin/students", json={"name": "Сидоров Пётр", "groupIds": ["GRP-0001"]})
    assert status == 200 and r["id"] == "STU-0003"
    assert _call(app, "POST", "/api/admin/students", json={"name": "", "groupIds": []})[0] == 400
    assert _call(app, "PATCH", "/api/admin/students/STU-0003", json={"name": "Сидоров Петр"})[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0003")[1]["name"] == "Сидоров Петр"

    status, c = _call(app, "GET", "/api/admin/students/STU-0001/partner-candidates")
    assert status == 200 and [x["name"] for x in c["candidates"]] == ["Петрова Анна", "Сидоров Петр"]
    assert _call(app, "PUT", "/api/admin/students/STU-0001/partner", json={"partnerId": "STU-0002"})[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0002")[1]["partner"]["id"] == "STU-0001"
    assert _call(app, "PUT", "/api/admin/students/STU-0001/partner", json={"partnerId": None})[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0002")[1]["partner"] is None
    assert _call(app, "PUT", "/api/admin/students/STU-0001/partner", json={"partnerId": "STU-0001"})[0] == 400

    dp["group_repo"].items.append(mk_group("GRP-0009", "ЮБ Малыши", billing_mode=GroupBillingMode.PER_VISIT,
                                            price_full=700, price_short=500))
    assert _call(app, "PUT", "/api/admin/students/STU-0003/groups", json={"groupId": "GRP-0009", "member": True})[0] == 200
    assert [g["name"] for g in _call(app, "GET", "/api/admin/students/STU-0003")[1]["groups"]] == ["БП Джаз", "ЮБ Малыши"]
    assert _call(app, "PUT", "/api/admin/students/STU-0003/groups", json={"groupId": "GRP-0001", "member": False})[1]["result"] == "removed"

    status, t = _call(app, "POST", "/api/admin/students/STU-0003/tier")
    assert status == 200 and t["tier"] == StudentGroupTier.SHORT.value
    dp["student_group_repo"].rows = [r for r in dp["student_group_repo"].rows if r.student_id != "STU-0003"]
    assert _call(app, "POST", "/api/admin/students/STU-0003/tier")[1]["error"] == "no_groups"

    status, r = _call(app, "PUT", "/api/admin/students/STU-0001/client", json={"name": "Иванова Мария", "phone": "+79990000000"})
    assert status == 200 and r["clientId"] == "CLT-0001"
    assert _call(app, "GET", "/api/admin/clients?q=иванова")[1]["clients"] == [
        {"id": "CLT-0001", "name": "Иванова Мария", "phone": "+79990000000", "tgId": None, "students": ["Иванов Иван"]}]
    assert _call(app, "PUT", "/api/admin/students/STU-0002/client", json={"clientId": "CLT-0001"})[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0002")[1]["client"]["name"] == "Иванова Мария"
    assert _call(app, "PUT", "/api/admin/students/STU-0002/client", json={"clientId": "CLT-0404"})[0] == 404

    dp["student_repo"].items[0].athlete_tg_id = 777
    assert _call(app, "GET", "/api/admin/students/STU-0001")[1]["isAthlete"] is True
    assert _call(app, "DELETE", "/api/admin/students/STU-0001/athlete")[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0001")[1]["isAthlete"] is False

    assert _call(app, "DELETE", "/api/admin/students/STU-0003")[0] == 200
    assert _call(app, "GET", "/api/admin/students/STU-0003")[0] == 404


def test_teacher_crud_and_open_period(api):
    app, dp = api
    body = {"tgId": 424242, "name": "Громов Илья", "rates": {"group": 1900, "teacher": 1900, "student": 2600}}
    status, r = _call(app, "POST", "/api/admin/teachers", json=body)
    assert status == 200 and r == {"id": "TCH-0002", "linked": True}
    assert next(u for u in dp["user_repo"].items if u.tg_id == 424242).teacher_id == "TCH-0002"
    assert _call(app, "POST", "/api/admin/teachers", json={"name": "x", "rates": {"group": 1}})[0] == 400

    assert _call(app, "PATCH", "/api/admin/teachers/TCH-0002", json={"rates": {"group": 2000, "teacher": 2000, "student": 2700}})[0] == 200
    assert _call(app, "GET", "/api/admin/teachers/TCH-0002")[1]["rates"] == {"group": 2000, "teacher": 2000, "student": 2700}
    assert _call(app, "PUT", "/api/admin/teachers/TCH-0002/groups", json={"groupId": "GRP-0001", "assigned": True})[0] == 200
    assert [g["name"] for g in _call(app, "GET", "/api/admin/teachers/TCH-0002")[1]["groups"]] == ["БП Джаз"]

    assert _call(app, "GET", "/api/admin/teachers/TCH-0001")[1]["submitted"] == ["2026-08"]
    assert _call(app, "POST", "/api/admin/teachers/TCH-0001/periods/2026-08/open")[0] == 200
    assert _call(app, "GET", "/api/admin/teachers/TCH-0001")[1]["submitted"] == []
    assert _call(app, "POST", "/api/admin/teachers/TCH-0001/periods/2026-08/open")[0] == 404

    assert _call(app, "DELETE", "/api/admin/teachers/TCH-0002")[0] == 200
    assert _call(app, "GET", "/api/admin/teachers/TCH-0002")[0] == 404
    assert all(u.tg_id != 424242 for u in dp["user_repo"].items) and "TCH-0002" not in dp["teacher_group_repo"]._t2g
    assert ADMIN_TG in [u.tg_id for u in dp["user_repo"].items]
