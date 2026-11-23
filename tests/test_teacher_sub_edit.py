"""Абонемент на месяц от педагога без счетов (SUBSCRIPTION_EDIT_GROUPS, решение владельца 09.10.2026):
только в разрешённой своей группе; блок «Абонемент за месяц» в карточке ученика."""
import asyncio

import pytest

from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services.subscription_recalculation import SubscriptionRecalculations
from config.settings import settings
from tests.fakes import FakeBot, mk_group, mk_lesson, mk_payment
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


RECALC = "/api/teacher/bills/student/STU-0001/subscription/recalculation"
CERTIFICATE = {"ym": YM, "groupId": "GRP-0025", "start": "2026-10-23", "end": "2026-11-02", "missed": 3}


@pytest.fixture()
def pilot(world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "subscription_edit_groups", "")
    monkeypatch.setattr(settings, "subscription_recalc_teacher_ids", None)
    monkeypatch.setattr(settings, "schedule_pilot_teacher_id", "TCH-0001")
    monkeypatch.setattr(settings, "owner_teacher_ids", "TCH-0001")
    world["payment_service"].subscription_recalculations = SubscriptionRecalculations(tmp_path / "recalc.sqlite3")
    return world


def test_certificate_preview_confirmation_and_retry(pilot):
    dp = pilot
    card = _call(dp, "GET", "/api/teacher/students/STU-0001")[1]
    target = next(s for s in card["subscriptions"] if s["groupId"] == "GRP-0025")
    assert target["canRecalculate"] and not target["canEdit"]
    before = asyncio.run(dp["payment_repo"].get_all())
    status, quote = _call(dp, "POST", RECALC, json=CERTIFICATE)
    assert status == 200 and (quote["base"], quote["amount"], quote["rest"]) == (5500, 3437, 3437)
    assert asyncio.run(dp["payment_repo"].get_all()) == before
    body = {**CERTIFICATE, "quote": quote["quote"]}
    assert _call(dp, "POST", RECALC, json={**body, "start": "2026-10-24"})[1]["error"] == "conflict"
    assert _call(dp, "POST", RECALC, json=body)[1]["applied"]
    assert _call(dp, "POST", RECALC, json=body)[1]["duplicate"]
    assert _call(dp, "POST", RECALC, json={**CERTIFICATE, "ym": "2026-12"})[1]["error"] == "overlap"
    assert not dp["subscription_override_repo"].items

    service = dp["payment_service"]
    service.subscription_recalculations = SubscriptionRecalculations(service.subscription_recalculations.path)
    ledger = asyncio.run(service.compute_ledger_map(since_period=YM, until_period=YM))
    assert ledger[("STU-0001", "SUB:GRP-0025", YM)] == (3437, 0)
    assert ledger[("STU-0001", "SUB:GRP-0031", YM)] == (5500, 0)
    assert [p.total_amount for p in asyncio.run(dp["payment_repo"].get_all())
            if p.teacher_id == "SUB:GRP-0025" and p.period_month == YM] == [3437]


def test_certificate_respects_manual_price_and_multiple_absences(pilot):
    dp = pilot
    override = dp["subscription_override_repo"]
    asyncio.run(override.upsert("GRP-0025", YM, "STU-0001", 4000))
    _, quote = _call(dp, "POST", RECALC, json=CERTIFICATE)
    assert quote["amount"] == 2500
    assert _call(dp, "POST", RECALC, json={**CERTIFICATE, "quote": quote["quote"]})[0] == 200
    body = {**CERTIFICATE, "start": "2026-11-12", "end": "2026-11-14", "missed": 5}
    _, second = _call(dp, "POST", RECALC, json=body)
    assert (second["base"], second["before"], second["amount"]) == (4000, 2500, 0)
    assert _call(dp, "POST", RECALC, json={**body, "quote": second["quote"]})[0] == 200
    assert [p.total_amount for p in asyncio.run(dp["payment_repo"].get_all())
            if p.teacher_id == "SUB:GRP-0025" and p.period_month == YM] == [0]
    asyncio.run(override.upsert("GRP-0025", YM, "STU-0001", 4200))
    charges = asyncio.run(dp["payment_service"].compute_bills_for_student_period("STU-0001", YM))
    assert charges["SUB:GRP-0025"].total == 4200


def test_certificate_pilot_access_and_paid_check(pilot, monkeypatch):
    dp = pilot
    _, quote = _call(dp, "POST", RECALC, json=CERTIFICATE)
    asyncio.run(dp["payment_repo"].add_new(mk_payment("PAY-SUB", "STU-0001", YM, "SUB:GRP-0025", 4000, PaymentStatus.PAID)))
    status, result = _call(dp, "POST", RECALC, json={**CERTIFICATE, "quote": quote["quote"]})
    assert status == 409 and result["error"] == "paid_more"
    assert not asyncio.run(dp["payment_service"].subscription_recalculations.rows())
    assert _call(dp, "POST", RECALC, json={**CERTIFICATE, "missed": True})[0] == 400
    monkeypatch.setattr(settings, "subscription_recalc_teacher_ids", "TCH-0002")
    assert _call(dp, "POST", RECALC, json=CERTIFICATE)[0] == 403
    assert _call(dp, "GET", "/api/teacher/students/STU-0001")[1]["subscriptions"] == []
