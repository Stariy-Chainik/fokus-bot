"""«🗑 Удалить ученика» в карточке = ушёл из школы (решение владельца 21.10.2026): у администратора — со всех групп,
у педагога — со своих; карточка и история остаются, удалить карточку совсем — отдельный второй шаг."""
import asyncio

from bot.models.enums import GroupBillingMode
from bot.services.membership import leave_school
from tests.fakes import FakeBot, StudentGroupRepoFake, mk_group
from tests.test_admin_api import ADMIN_TG, YM
from tests.test_admin_api import _call as _acall
from tests.test_teacher_api import _call, api  # noqa: F401 — педагог TCH-0001 с группой GRP-0001


def _groups(dp, sid):
    return {x.group_id: x for x in asyncio.run(dp["student_group_repo"].get_all()) if x.student_id == sid}


def test_leave_school_marks_subscription_and_removes_per_visit():
    sg = StudentGroupRepoFake([("STU-1", "GRP-S", "2026-04", ""), ("STU-1", "GRP-P", "2026-04", ""),
                               ("STU-1", "GRP-X", "2026-04", ""), ("STU-2", "GRP-S", "2026-04", "")])

    class Groups:
        async def get_by_id(self, gid):
            return mk_group(gid, billing_mode=GroupBillingMode.SUBSCRIPTION if gid == "GRP-S" else GroupBillingMode.PER_VISIT)
    r = asyncio.run(leave_school("STU-1", "2026-11", Groups(), sg, only_groups={"GRP-S", "GRP-P"}, who="педагог Река"))
    assert (r.marked, r.removed, r.left_period) == (["GRP-S"], ["GRP-P"], "2026-11")
    rows = {x.group_id: x for x in sg.rows if x.student_id == "STU-1"}
    assert rows["GRP-S"].left_period == "2026-11" and "GRP-P" not in rows and rows["GRP-X"].is_active   # чужая не тронута
    assert sg.rows[-1].student_id == "STU-2" and sg.rows[-1].is_active


def test_admin_leave_school_then_card_and_list_show_departed(api):  # noqa: F811
    app, dp = api
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000
    dp["group_repo"].items.append(mk_group("GRP-0002", "БП Хип-хоп", billing_mode=GroupBillingMode.PER_VISIT, price_full=800))
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0002"))
    card = _acall(app, "GET", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[1]
    assert len(card["leaveOptions"]) == 2 and card["left"] == [] and card["departed"] is False
    nxt = card["leaveOptions"][1]["ym"]

    assert _acall(app, "POST", "/api/admin/students/STU-0002/leave", tg_id=ADMIN_TG, json={"leftPeriod": "2020-01"})[0] == 400
    st, r = _acall(app, "POST", "/api/admin/students/STU-0002/leave", tg_id=ADMIN_TG, json={"leftPeriod": nxt})
    assert st == 200 and (r["marked"], r["removed"], r["leftPeriod"]) == (["GRP-0001"], ["GRP-0002"], nxt)
    rows = _groups(dp, "STU-0002")
    assert rows["GRP-0001"].left_period == nxt and "GRP-0002" not in rows
    # карточка на месте: ушедшая группа с месяцем, активных нет → departed; в списке — «ушёл»
    card = _acall(app, "GET", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[1]
    assert card["departed"] is True and card["groups"] == [] and card["left"] == [
        {"id": "GRP-0001", "name": g.name, "leftPeriod": nxt}]
    lst = _acall(app, "GET", "/api/admin/students?q=петрова", tg_id=ADMIN_TG)[1]["students"]
    assert lst and lst[0]["left"] is True and lst[0]["groups"] == []
    # повтор — групп нет (409); «↩ Вернуть» снимает пометку
    assert _acall(app, "POST", "/api/admin/students/STU-0002/leave", tg_id=ADMIN_TG, json={"leftPeriod": nxt})[0] == 409
    assert _acall(app, "PUT", "/api/admin/students/STU-0002/groups", tg_id=ADMIN_TG,
                  json={"groupId": "GRP-0001", "member": True})[0] == 200
    assert _groups(dp, "STU-0002")["GRP-0001"].is_active
    assert _acall(app, "GET", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[1]["departed"] is False
    assert _acall(app, "POST", "/api/admin/students/STU-0404/leave", tg_id=ADMIN_TG, json={"leftPeriod": YM})[0] == 404
    # карточку совсем удаляет по-прежнему DELETE — второй шаг листа
    assert _acall(app, "DELETE", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[0] == 200


def test_teacher_leave_school_only_own_groups_and_notifies_admins(api):  # noqa: F811
    app, dp = api
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000
    dp["group_repo"].items.append(mk_group("GRP-0002", "ВБ ХГ Младшая", billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=5000))
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0002"))        # группа другого педагога
    st, o = _call(app, "GET", "/api/teacher/students/STU-0002/leave")
    assert st == 200 and [(x["id"], x["subscription"]) for x in o["groups"]] == [("GRP-0001", True)]
    assert o["others"] == 1 and len(o["leaveOptions"]) == 2
    assert _call(app, "POST", "/api/teacher/students/STU-0002/leave", json={"leftPeriod": "2020-01"})[0] == 400

    bot = FakeBot()
    st, r = _call(app, "POST", "/api/teacher/students/STU-0002/leave", bot=bot, json={"leftPeriod": YM})
    assert st == 200 and (r["marked"], r["removed"], r["remaining"], r["debt"]) == (["GRP-0001"], [], 1, 6800)
    rows = _groups(dp, "STU-0002")
    assert rows["GRP-0001"].left_period == YM and rows["GRP-0002"].is_active          # чужая группа не тронута
    text = bot.sent[0][1]
    assert "ушёл из школы" in text and "Петрова Анна" in text and "других педагогов: 1" in text
    assert "Неоплачено 6800 ₽" in text and "в должниках не считается" in text
    # ученик больше не виден педагогу: повтор и чужой — 404
    assert _call(app, "POST", "/api/teacher/students/STU-0002/leave", json={"leftPeriod": YM})[0] == 404
    assert _call(app, "GET", "/api/teacher/students/STU-0002/leave")[0] == 404


def test_teacher_leave_school_per_visit_without_month(api):  # noqa: F811
    app, dp = api
    bot = FakeBot()
    st, r = _call(app, "POST", "/api/teacher/students/STU-0001/leave", bot=bot, json={})
    assert st == 200 and (r["marked"], r["removed"], r["remaining"]) == ([], ["GRP-0001"], 0)
    assert "GRP-0001" not in _groups(dp, "STU-0001")
    assert "Других групп нет" in bot.sent[0][1] and "Убран из состава" in bot.sent[0][1]
