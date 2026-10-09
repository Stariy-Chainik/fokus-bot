"""Абонемент на месяц от педагога без счетов (SUBSCRIPTION_EDIT_GROUPS, решение владельца 09.10.2026):
только в разрешённой своей группе; блок «Абонемент за месяц» в карточке ученика."""
import asyncio

import pytest

from bot.models.enums import GroupBillingMode
from config.settings import settings
from bot.models.enums import LessonType
from tests.fakes import FakeBot, mk_group, mk_lesson
from tests.test_admin_api import ADMIN_TG, YM
from tests.test_teacher_api import _call, api, _receipt_rc  # noqa: F401  (фикстуры)


@pytest.fixture()
def world(api, monkeypatch):  # noqa: F811
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "")                 # педагог без счетов
    for gid, name in (("GRP-0025", "БП БТ Первый год"), ("GRP-0031", "Другая абонементная")):
        dp["group_repo"].items.append(mk_group(gid, name, billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=5500))
        asyncio.run(dp["teacher_group_repo"].add("TCH-0001", gid))
        asyncio.run(dp["student_group_repo"].add("STU-0001", gid))
        teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))     # абонемент — с первого занятия группы
        dp["lesson_repo"].items.append(mk_lesson(f"LES-{gid}", teacher, f"{YM}-02", 60, LessonType.GROUP, group_id=gid))
    return dp


def test_no_right_without_setting(world, monkeypatch):
    dp = world
    monkeypatch.setattr(settings, "subscription_edit_groups", "")
    card = _call(dp, "GET", "/api/teacher/students/STU-0001")[1]
    assert card["subscriptions"] == []
    body = {"ym": YM, "groupId": "GRP-0025", "amount": 2750}
    assert _call(dp, "PUT", "/api/teacher/bills/student/STU-0001/subscription", json=body)[0] == 404


def test_teacher_sets_month_subscription_in_allowed_group(world, monkeypatch):
    dp = world
    monkeypatch.setattr(settings, "subscription_edit_groups", "TCH-0001:GRP-0025")
    card = _call(dp, "GET", "/api/teacher/students/STU-0001")[1]
    assert card["subscriptions"] == [{"groupId": "GRP-0025", "name": "БП БТ Первый год", "total": 5500, "paid": 0}]
    bot = FakeBot()
    body = {"ym": YM, "groupId": "GRP-0025", "amount": 2750, "reason": "пришла с 15 числа"}
    status, r = _call(dp, "PUT", "/api/teacher/bills/student/STU-0001/subscription", json=body, bot=bot)
    assert status == 200 and (r["old"], r["amount"]) == (5500, 2750)
    assert _call(dp, "GET", "/api/teacher/students/STU-0001")[1]["subscriptions"][0]["total"] == 2750
    assert bot.sent[0][0] == ADMIN_TG and "изменил абонемент" in bot.sent[0][1] and "пришла с 15 числа" in bot.sent[0][1]
    # другая группа педагога без разрешения и счета — по-прежнему закрыты
    assert _call(dp, "PUT", "/api/teacher/bills/student/STU-0001/subscription",
                 json={**body, "groupId": "GRP-0031"})[0] == 404
    assert _call(dp, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[0] == 403
