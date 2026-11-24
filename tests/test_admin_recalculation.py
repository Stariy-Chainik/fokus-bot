"""Перерасчёт по справке из административного кабинета."""
import asyncio

import pytest
from aiohttp import web

from bot.api.teacher import register_teacher_api
from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services.subscription_recalculation import SubscriptionRecalculations
from tests.fakes import mk_group, mk_lesson, mk_payment, mk_student
from tests.test_admin_api import YM, _call, make_api
from tests.test_teacher_api import TEACHER_TG, _call as call_teacher


RECALC = "/api/admin/recalculation/STU-0001"
SEARCH = "/api/admin/recalculation/search"
CERTIFICATE = {"ym": YM, "groupId": "GRP-0025", "start": "2026-10-23", "end": "2026-11-02", "missed": 3}


@pytest.fixture()
def world(monkeypatch, tmp_path):
    dp, _ = make_api(monkeypatch)
    dp["payment_service"].subscription_recalculations = SubscriptionRecalculations(tmp_path / "recalc.sqlite3")
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    for gid, name in (("GRP-0025", "БП БТ Первый год"), ("GRP-0031", "Другая абонементная")):
        dp["group_repo"].items.append(mk_group(gid, name, billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=5500))
        asyncio.run(dp["student_group_repo"].add("STU-0001", gid))
        dp["lesson_repo"].items.append(mk_lesson(f"LES-{gid}", teacher, f"{YM}-02", 60, LessonType.GROUP, group_id=gid))
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    return dp


def test_certificate_preview_confirmation_and_retry(world):
    dp = world
    assert _call(dp, "GET", "/api/admin/home")[1]["canRecalculate"] is True
    assert "GRP-0025" not in asyncio.run(dp["teacher_group_repo"].get_groups_for_teacher("TCH-0001"))
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


def test_certificate_respects_manual_price_and_multiple_absences(world):
    dp = world
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


def test_certificate_protects_already_paid_amount(world):
    dp = world
    _, quote = _call(dp, "POST", RECALC, json=CERTIFICATE)
    asyncio.run(dp["payment_repo"].add_new(mk_payment("PAY-SUB", "STU-0001", YM, "SUB:GRP-0025", 4000, PaymentStatus.PAID)))
    status, result = _call(dp, "POST", RECALC, json={**CERTIFICATE, "quote": quote["quote"]})
    assert status == 409 and result["error"] == "paid_more"
    assert not asyncio.run(dp["payment_service"].subscription_recalculations.rows())
    assert _call(dp, "POST", RECALC, json={**CERTIFICATE, "missed": True})[0] == 400


def test_admin_search_includes_unassigned_groups_and_archived_students(world):
    dp = world
    dp["student_repo"].items.extend([
        mk_student("STU-0003", "Иванова Мария", archived=f"{YM}-01"),
        mk_student("STU-0004", "Иванова Анна"),
    ])
    asyncio.run(dp["student_group_repo"].add("STU-0003", "GRP-0031"))
    assert _call(dp, "GET", SEARCH)[1]["students"] == []
    status, result = _call(dp, "GET", SEARCH + "?q=ИВАНОВ")
    assert status == 200
    found = {s["id"]: ({g["id"] for g in s["groups"]}, s["archived"]) for s in result["students"]}
    assert found == {
        "STU-0001": ({"GRP-0025", "GRP-0031"}, False),
        "STU-0003": ({"GRP-0031"}, True),
        "STU-0004": (set(), False),
    }
    assert _call(dp, "POST", RECALC.replace("STU-0001", "STU-0004"), json=CERTIFICATE)[0] == 404


def test_recalculation_is_available_only_in_admin_mode(world):
    dp = world
    assert _call(dp, "GET", SEARCH + "?q=ИВАНОВ", tg_id=TEACHER_TG)[0] == 403
    assert _call(dp, "POST", RECALC, tg_id=TEACHER_TG, json=CERTIFICATE)[0] == 403
    assert "canRecalculate" not in call_teacher(dp, "GET", "/api/teacher/home")[1]
    app = web.Application()
    register_teacher_api(app, dp)
    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/api/teacher/students/recalculation-search" not in paths
    assert "/api/teacher/bills/student/{sid}/subscription/recalculation" not in paths
