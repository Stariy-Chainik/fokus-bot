"""«🔄 Перевести в другую группу» у педагога (решение владельца 16.10.2026): из своей группы в свою одним действием."""
import asyncio

from bot.models.enums import GroupBillingMode
from tests.fakes import FakeBot, mk_group
from tests.test_teacher_api import _call, api  # noqa: F401 — фикстура педагога TCH-0001 с группой GRP-0001


def _second_group(dp, mode=GroupBillingMode.SUBSCRIPTION):
    dp["group_repo"].items.append(mk_group("GRP-0002", "БП Хип-хоп", billing_mode=mode, price_full=5000))
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0002"))


def test_options_and_transfer_from_subscription_group(api):  # noqa: F811
    app, dp = api
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000
    _second_group(dp)
    st, o = _call(app, "GET", "/api/teacher/students/STU-0002/transfer")
    assert st == 200 and [(x["id"], x["subscription"]) for x in o["from"]] == [("GRP-0001", True)]
    assert [x["id"] for x in o["to"]] == ["GRP-0002"] and len(o["leaveOptions"]) == 2

    bot = FakeBot()
    st, r = _call(app, "POST", "/api/teacher/students/STU-0002/transfer", bot=bot,
                  json={"fromGroupId": "GRP-0001", "toGroupId": "GRP-0002", "leftPeriod": o["leaveOptions"][1]["ym"]})
    assert st == 200 and (r["result"], r["toName"], r["debt"]) == ("marked", "БП Хип-хоп", 6800)   # долг не мешает
    rows = {(x.group_id): x for x in asyncio.run(dp["student_group_repo"].get_all()) if x.student_id == "STU-0002"}
    assert rows["GRP-0002"].left_period == "" and rows["GRP-0001"].left_period == o["leaveOptions"][1]["ym"]
    assert bot.sent and "перевёл ученика" in bot.sent[0][1] and "Петрова Анна" in bot.sent[0][1] and "Долг ученика: 6800" in bot.sent[0][1]
    # повтор: ученик уже там — 409; обратно из группы, где его больше нет (ушёл), — 404
    assert _call(app, "POST", "/api/teacher/students/STU-0002/transfer",
                 json={"fromGroupId": "GRP-0001", "toGroupId": "GRP-0002"})[0] in (404, 409)


def test_transfer_guards(api):  # noqa: F811
    app, dp = api
    _second_group(dp, GroupBillingMode.PER_VISIT)
    post = lambda body: _call(app, "POST", "/api/teacher/students/STU-0001/transfer", json=body)[0]   # noqa: E731
    assert post({"fromGroupId": "GRP-0001", "toGroupId": "GRP-0099"}) == 404           # чужая/несуществующая группа
    assert post({"fromGroupId": "GRP-0001", "toGroupId": "GRP-0001"}) == 404           # та же группа
    assert _call(app, "GET", "/api/teacher/students/STU-9999/transfer")[0] == 404
    # не абонемент: месяц не нужен, строка старой группы удаляется
    st, r = _call(app, "POST", "/api/teacher/students/STU-0001/transfer", json={"fromGroupId": "GRP-0001", "toGroupId": "GRP-0002"})
    assert st == 200 and r["result"] == "removed"
    gids = asyncio.run(dp["student_group_repo"].get_groups_for_student("STU-0001"))
    assert gids == ["GRP-0002"]
