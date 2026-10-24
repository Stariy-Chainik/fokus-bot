"""Индивидуальные Яковлевой (REVENUE_SHARE_GROUPS): своя сумма с каждой ученицы и ученица из любой группы школы
(решение владельца 24.10.2026). Сумма — в снимке attendees: счёт родителю = сумма, зарплата педагогу = 50 %."""
import asyncio

from bot.models.enums import GroupBillingMode
from bot.services.billing_service import calc_earned
from bot.utils.attendees import parse_attendees
from config.settings import settings
from tests.fakes import mk_group, mk_student
from tests.test_teacher_api import _call, api  # noqa: F401 — педагог TCH-0001 с группой GRP-0001


def _setup(dp, monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")                      # счета — как у Яковлевой
    dp["group_repo"].items.append(mk_group("GRP-0020", "ХГ Индивидуальные", billing_mode=GroupBillingMode.PER_VISIT,
                                           price_full=1800))
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0020"))
    dp["student_repo"].items.append(mk_student("STU-0009", "Чужая Ученица"))              # не из групп педагога


def test_custom_amount_and_outside_student(api, monkeypatch):  # noqa: F811
    app, dp = api
    _setup(dp, monkeypatch)
    opts = _call(app, "GET", "/api/teacher/record/options")[1]
    assert opts["rshareGroupId"] == "GRP-0020" and "rshare" in opts["kinds"]
    assert _call(app, "GET", "/api/teacher/students/STU-0009")[0] == 404                      # пока не видна
    body = {"kind": "rshare", "date": "2026-10-20", "durationMin": 60, "studentIds": ["STU-0002", "STU-0009"],
            "amounts": {"STU-0002": 2500, "STU-0009": ""}}
    st, r = _call(app, "POST", "/api/teacher/record", json=body)
    assert st == 200 and r["created"] == 1
    ls = asyncio.run(dp["lesson_repo"].get_by_id(r["lessons"][0]))
    amounts = {e.student_id: e.amount for e in parse_attendees(ls.attendees)}
    assert amounts == {"STU-0002": 2500, "STU-0009": 1800}                                    # пусто — цена группы
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    assert calc_earned(ls.type, ls.duration_min, teacher, ls.group_id, ls.attendees, ls.date) == (2500 + 1800) // 2
    # ученица из чужой группы теперь в техгруппе — педагог видит её карточку и счёт
    assert "GRP-0020" in asyncio.run(dp["student_group_repo"].get_groups_for_student("STU-0009"))
    assert _call(app, "GET", "/api/teacher/students/STU-0009")[0] == 200
    st, bill = _call(app, "GET", "/api/teacher/bills/student/STU-0009?ym=2026-10")
    assert st == 200 and sum(r["total"] for r in bill["rows"]) == 1800                       # счёт за её занятие
    # своя ученица в техгруппу не добавляется
    assert "GRP-0020" not in asyncio.run(dp["student_group_repo"].get_groups_for_student("STU-0002"))


def test_bad_amount_rejected(api, monkeypatch):  # noqa: F811
    app, dp = api
    _setup(dp, monkeypatch)
    for bad in (-1, "abc", 10**6):
        st, r = _call(app, "POST", "/api/teacher/record", json={
            "kind": "rshare", "date": "2026-10-20", "durationMin": 60, "studentIds": ["STU-0002"], "amounts": {"STU-0002": bad}})
        assert st == 400, bad
