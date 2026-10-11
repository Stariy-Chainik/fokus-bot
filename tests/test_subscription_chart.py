"""Диаграмма «Абонементы: собрано из плана» на сводке администратора (решение владельца 11.10.2026)."""
import asyncio

from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services.payment_service import period_collection, subscription_collection
from tests.fakes import mk_group, mk_lesson, mk_payment
from tests.test_admin_api import YM, _call, make_api


def test_subscription_collection_by_group_caps_overpayment():
    ledger = {
        ("S1", "SUB:G1", "2026-10"): (6000, 6000),     # оплачено полностью
        ("S2", "SUB:G1", "2026-10"): (6000, 9000),     # переплата не считается сбором
        ("S3", "SUB:G1", "2026-10"): (6000, 2000),     # частично
        ("S4", "SUB:G2", "2026-10"): (5500, 0),
        ("S1", "TCH-1", "2026-10"): (2000, 2000),      # занятия — не абонемент
        ("S1", "SUB:G1", "2026-09"): (6000, 0),        # другой месяц
    }
    assert subscription_collection(ledger, "2026-10") == {"G1": (18000, 14000, 3, 2), "G2": (5500, 0, 1, 0)}
    total = period_collection({k: v for k, v in ledger.items() if k[1].startswith("SUB:")}, "2026-10")
    assert (total.accrued, total.paid) == (23500, 14000)          # сумма по группам = абонементная часть сбора


def test_admin_home_returns_subscription_chart(monkeypatch):
    dp, _ = make_api(monkeypatch)
    dp["group_repo"].items.append(mk_group("GRP-0002", "Азбука", billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=3000))
    asyncio.run(dp["student_group_repo"].add("STU-0001", "GRP-0002"))
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0002"))
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    dp["lesson_repo"].items.append(mk_lesson("LES-SUB", teacher, f"{YM}-02", 60, LessonType.GROUP, group_id="GRP-0002"))
    dp["payment_repo"].rows.append(mk_payment("PAY-SUB", "STU-0001", YM, teacher_id="SUB:GRP-0002", amount=3000, status=PaymentStatus.PAID))
    s = _call(dp, "GET", f"/api/admin/home?ym={YM}")[1]["subscriptions"]
    assert (s["plan"], s["fact"]) == (6000, 3000)
    assert s["groups"] == [{"id": "GRP-0002", "name": "Азбука", "plan": 6000, "fact": 3000, "students": 2, "paidFull": 1}]
