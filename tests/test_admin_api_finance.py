"""API кабинета администратора, этап 2: прибыль, ручные записи, зарплаты, выплаты, история оплат, напоминание должникам."""
import asyncio

import pytest

from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from tests.fakes import mk_group, mk_lesson, mk_payment
from tests.test_admin_api import YM, _call, make_api

PARENT_TG = 5037902894


@pytest.fixture()
def api(monkeypatch):
    return make_api(monkeypatch)


def test_profit_month_and_manual_entries(api):
    app, dp = api
    status, p = _call(app, "GET", f"/api/admin/profit?ym={YM}")
    assert status == 200
    row = p["rows"][0]
    assert (row["name"], row["income"], row["salary"]) == ("Река Станислав", 5600, 4333)
    assert (row["groupLessons"], row["individualLessons"]) == (1, 2)
    assert p["totals"]["totalIncome"] == 5600 and p["totals"]["profit"] == 1267 and p["subscriptions"] == []

    status, r = _call(app, "POST", "/api/admin/finance", json={"periodMonth": YM, "kind": "income", "title": "Турнир", "amount": 15000})
    assert status == 200 and r["id"] == "FIN-000001"
    assert _call(app, "POST", "/api/admin/finance", json={"periodMonth": YM, "kind": "bonus", "title": "x", "amount": 1})[0] == 400
    status, p = _call(app, "GET", f"/api/admin/profit?ym={YM}")
    assert p["finance"] == [{"id": "FIN-000001", "kind": "income", "title": "Турнир", "amount": 15000}]
    assert p["totals"]["manualIncome"] == 15000 and p["totals"]["profit"] == 1267 + 15000
    assert _call(app, "DELETE", "/api/admin/finance/FIN-000001")[0] == 200
    assert _call(app, "DELETE", "/api/admin/finance/FIN-000001")[0] == 404


def test_profit_day_and_teacher_detail(api):
    app, _ = api
    status, d = _call(app, "GET", f"/api/admin/profit/day?date={YM}-12")
    assert status == 200 and d["rows"][0]["income"] == 1600 and d["totals"]["salary"] == 1333
    status, t = _call(app, "GET", f"/api/admin/profit/teacher/TCH-0001?period={YM}")
    assert status == 200 and [x["income"] for x in t["lessons"]] == [2000, 2000, 1600] and t["profit"] == 1267
    assert _call(app, "GET", f"/api/admin/profit/teacher/TCH-0404?period={YM}")[0] == 404


def test_salaries_and_lines(api):
    app, _ = api
    status, s = _call(app, "GET", f"/api/admin/salaries?ym={YM}")
    assert status == 200 and s["teachers"][0]["accrued"] == 4333 and s["teachers"][0]["lessons"] == 3 and s["total"] == 4333
    status, t = _call(app, "GET", f"/api/admin/salaries/TCH-0001?ym={YM}")
    assert status == 200 and [ln["amount"] for ln in t["lines"]] == [1500, 1500, 1333] and t["total"] == 4333


def test_payouts_overrides_flow(api):
    app, dp = api
    status, p = _call(app, "GET", f"/api/admin/payouts?ym={YM}")
    assert status == 200 and p["teachers"][0]["status"] == "none" and p["accrued"] == 4333
    body = {"teacherId": "TCH-0001", "periodMonth": YM, "amount": 2000, "comment": "аванс"}
    status, r = _call(app, "POST", "/api/admin/payouts", json=body)
    assert status == 200 and r["id"] == "PO-000001"
    status, p = _call(app, "GET", f"/api/admin/payouts?ym={YM}")
    assert p["teachers"][0]["status"] == "partial" and p["paid"] == 2000
    status, t = _call(app, "GET", f"/api/admin/payouts/TCH-0001?ym={YM}")
    assert t["paid"] == 2000 and t["payouts"][0]["comment"] == "аванс" and len(t["lines"]) == 3
    assert _call(app, "POST", "/api/admin/payouts", json={"teacherId": "TCH-0001", "periodMonth": YM, "amount": 0})[0] == 400
    assert _call(app, "POST", "/api/admin/payouts", json={"teacherId": "TCH-0404", "periodMonth": YM, "amount": 10})[0] == 404

    body = {"teacherId": "TCH-0001", "date": f"{YM}-20", "minutes": 120, "comment": "замена"}
    status, o = _call(app, "POST", "/api/admin/salary-overrides", json=body)
    assert status == 200 and o["id"] == "SO-000001"
    status, t = _call(app, "GET", f"/api/admin/payouts/TCH-0001?ym={YM}")
    assert t["overrides"] == [{"id": "SO-000001", "date": f"{YM}-20", "minutes": 120, "comment": "замена"}]
    assert _call(app, "DELETE", "/api/admin/salary-overrides/SO-000001")[0] == 200
    assert _call(app, "DELETE", "/api/admin/salary-overrides/SO-000001")[0] == 404


def test_payment_history(api):
    app, dp = api
    body = {"studentId": "STU-0001", "periodMonth": YM, "key": "TCH-0001", "amount": 2000, "method": "cash"}
    _call(app, "POST", "/api/admin/pay/confirm", json=body)
    status, s = _call(app, "GET", "/api/admin/payhist?q=иван")
    assert status == 200 and s["students"] == [{"id": "STU-0001", "name": "Иванов Иван", "payments": 1}]
    status, m = _call(app, "GET", "/api/admin/payhist/STU-0001")
    assert status == 200 and m["months"] == [{"period": YM, "paid": 2000, "pending": 2800}] and m["totalPaid"] == 2000
    status, r = _call(app, "GET", f"/api/admin/payhist/STU-0001/{YM}")
    assert status == 200 and [(x["amount"], x["method"]) for x in r["paid"]] == [(2000, "cash")] and r["pending"][0]["amount"] == 2800


def test_debtors_remind_sends_only_closed_months_to_linked_parents(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    from tests.fakes import mk_lesson
    dp["lesson_repo"].items.append(mk_lesson("LES-0", teacher, "2026-08-20", students=[("STU-0002", "Петрова Анна")]))
    status, r = _call(app, "POST", "/api/admin/debtors/remind")
    assert status == 200 and (r["sent"], r["failed"], r["skipped"]) == (0, 0, 1)   # у Петровой нет родителя в боте
    dp["student_repo"].items[1].parent_tg_ids.append(PARENT_TG)
    status, r = _call(app, "POST", "/api/admin/debtors/remind")
    assert status == 200 and (r["sent"], r["skipped"], r["total"]) == (1, 0, 2000)
    addrs, text = dp["notifier"].sent[-1]
    assert addrs == [("tg", PARENT_TG)] and "Петрова Анна" in text and "2000 ₽" in text and "08.2026" in text


def test_debtors_remind_without_bot(api):
    app, dp = api
    dp.pop("notifier")
    assert _call(app, "POST", "/api/admin/debtors/remind")[0] == 503


def test_profit_teacher_lessons_name_the_students(api):
    app, dp = api
    status, t = _call(app, "GET", f"/api/admin/profit/teacher/TCH-0001?period={YM}")
    assert status == 200
    by_id = {x["lessonId"]: x for x in t["lessons"]}
    assert by_id["LES-1"]["students"] == ["Иванов Иван"] and by_id["LES-1"]["groupName"] == ""
    assert by_id["LES-3"]["groupName"] == "БП Джаз" and by_id["LES-3"]["students"] == ["Иванов Иван", "Петрова Анна"]


def test_profit_subscription_group_roster_and_payments(api):
    app, dp = api
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    dp["group_repo"].items.append(mk_group("GRP-0002", "Азбука", billing_mode=GroupBillingMode.SUBSCRIPTION, price_full=3000))
    for sid in ("STU-0001", "STU-0002"):
        asyncio.run(dp["student_group_repo"].add(sid, "GRP-0002"))
    dp["lesson_repo"].items.append(mk_lesson("LES-9", teacher, f"{YM}-05", duration=60, lesson_type=LessonType.GROUP, group_id="GRP-0002"))
    dp["payment_repo"].rows.append(mk_payment("PAY-1", "STU-0001", YM, "SUB:GRP-0002", 3000, status=PaymentStatus.PAID))
    asyncio.run(dp["subscription_override_repo"].upsert("GRP-0002", "*", "STU-0002", 0))  # Петрова освобождена навсегда

    status, p = _call(app, "GET", f"/api/admin/profit?ym={YM}")
    assert status == 200 and p["subscriptions"] == [{"groupId": "GRP-0002", "groupName": "Азбука", "students": 1, "income": 3000}]

    status, d = _call(app, "GET", f"/api/admin/profit/subscription/GRP-0002?ym={YM}")
    assert status == 200 and (d["groupName"], d["price"], d["accrued"], d["paid"]) == ("Азбука", 3000, 3000, 3000)
    assert [(x["name"], x["accrued"], x["paid"], x["status"], x["active"]) for x in d["students"]] == [
        ("Иванов Иван", 3000, 3000, "paid", True), ("Петрова Анна", 0, 0, "exempt", True),
    ]
    # месяц без занятия группы — начислений нет, но оплата, если была, видна
    status, d = _call(app, "GET", "/api/admin/profit/subscription/GRP-0002?ym=2026-03")
    assert status == 200 and d["accrued"] == 0 and [x["status"] for x in d["students"]] == ["exempt", "exempt"]
    assert _call(app, "GET", f"/api/admin/profit/subscription/GRP-0001?ym={YM}")[0] == 404  # группа по посещению
    assert _call(app, "GET", f"/api/admin/profit/subscription/GRP-0404?ym={YM}")[0] == 404


def test_profit_breakdown_explains_income_and_profit_by_days(api):
    app, dp = api
    status, b = _call(app, "GET", f"/api/admin/profit/breakdown?ym={YM}")
    assert status == 200
    assert [(d["date"][8:], d["income"], d["salary"], d["profit"], d["lessons"]) for d in b["days"]] == [
        ("03", 2000, 1500, 500, 1), ("10", 2000, 1500, 500, 1), ("12", 1600, 1333, 267, 1),
    ]
    assert sum(d["income"] for d in b["days"]) == b["totals"]["lessonIncome"] == 5600
    assert sum(d["profit"] for d in b["days"]) == b["totals"]["profit"] == 1267
    assert [r["name"] for r in b["rows"]] == ["Река Станислав"] and b["subscriptions"] == [] and b["finance"] == []
