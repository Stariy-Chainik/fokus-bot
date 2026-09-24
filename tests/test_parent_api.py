"""API кабинета родителя (/api/parent/*): доступ к своим детям, счета, занятия, дневник, оплата."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_parent_api
from bot.models.enums import GroupBillingMode, PaymentStatus
from bot.services.diary_service import DiaryService
from config.settings import settings
from tests.fakes import AthleteTaskRepoFake, FakeBot, TrainingEntryRepoFake, mk_entry, mk_payment
from tests.test_admin_api import ADMIN_TG, PARENT_TG, YM, make_api
from tests.test_telegram_auth import make_init_data


def _call(dp, method, path, tg_id=PARENT_TG, json=None, headers=None, bot=None):
    async def run():
        app = web.Application()
        register_parent_api(app, dp, bot)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            h = headers if headers is not None else {"Authorization": f"tma {make_init_data(user_id=tg_id)}"}
            resp = await client.request(method, path, headers=h, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.fixture()
def api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    dp["entry_repo"] = TrainingEntryRepoFake([mk_entry("TE-1", "STU-0001", f"{YM}-05", 60, topics=["Джайв"], grade=4)])
    dp["task_repo"] = AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    return dp, dp


def test_only_parent_of_a_child_gets_in(api):
    app, dp = api
    status, me = _call(app, "GET", "/api/parent/me")
    assert status == 200 and [c["name"] for c in me["children"]] == ["Иванов Иван"]
    assert _call(app, "GET", "/api/parent/me", tg_id=ADMIN_TG)[0] == 403     # админ без детей
    assert _call(app, "GET", "/api/parent/me", headers={})[0] == 401


def test_home_and_bills_show_own_child_only(api):
    app, dp = api
    status, h = _call(app, "GET", "/api/parent/home")
    assert status == 200 and [c["name"] for c in h["children"]] == ["Иванов Иван"]
    assert h["rest"] == 4800 and h["children"][0]["thisMonth"]["accrued"] == 4800

    status, b = _call(app, "GET", "/api/parent/bills")
    assert status == 200 and [m["ym"] for m in b["months"]] == [YM]

    status, d = _call(app, "GET", f"/api/parent/bill/STU-0001/{YM}")
    assert status == 200 and d["accrued"] == 4800 and d["rest"] == 4800
    assert [r["key"] for r in d["rows"]] == ["TCH-0001"]
    assert [x["paid"] for x in d["rows"][0]["lessons"]] == [False, False, False]
    assert _call(app, "GET", f"/api/parent/bill/STU-0002/{YM}")[0] == 404    # чужой ребёнок


def test_paid_lessons_are_marked(api):
    app, dp = api
    dp["payment_repo"].rows.append(mk_payment("PAY-1", "STU-0001", YM, "TCH-0001", 2000, status=PaymentStatus.PAID))
    status, d = _call(app, "GET", f"/api/parent/bill/STU-0001/{YM}")
    assert status == 200 and d["paid"] == 2000 and d["rest"] == 2800
    assert [x["paid"] for x in d["rows"][0]["lessons"]] == [True, False, False]

    status, les = _call(app, "GET", f"/api/parent/lessons/STU-0001?ym={YM}")
    assert status == 200 and len(les["lessons"]) == 3      # расписание без сумм


def test_diary_is_read_only_for_parent(api):
    app, dp = api
    status, d = _call(app, "GET", f"/api/parent/diary/STU-0001?ym={YM}")
    assert status == 200 and d["stats"]["sessions"] == 1 and d["entries"][0]["grade"] == 4
    assert _call(app, "GET", f"/api/parent/diary/STU-0002?ym={YM}")[0] == 404


def test_cash_payment_notifies_admin(api):
    app, dp = api
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/parent/pay",
                      json={"studentId": "STU-0001", "ym": YM, "method": "cash"}, bot=bot)
    assert status == 200 and r["amount"] == 4800 and r["notified"] == 1
    assert bot.sent and "наличными" in bot.sent[0][1]
    # у админа кнопка подтверждения — как в боте, а не голый текст
    kb = bot.sent[0][2] if len(bot.sent[0]) > 2 else None
    buttons = [b.text for row in (kb.inline_keyboard if kb else []) for b in row]
    assert "✅ Подтвердить оплату" in buttons
    # без бота уведомить некому
    assert _call(app, "POST", "/api/parent/pay", json={"studentId": "STU-0001", "ym": YM, "method": "cash"})[0] == 503
    assert _call(app, "POST", "/api/parent/pay",
                 json={"studentId": "STU-0002", "ym": YM, "method": "cash"}, bot=bot)[0] == 404


def test_selected_lessons_become_the_payment_intent(api):
    app, dp = api
    status, d = _call(app, "GET", f"/api/parent/bill/STU-0001/{YM}")
    lesson_id = d["rows"][0]["lessons"][1]["id"]                    # второе занятие
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/parent/pay", bot=bot, json={
        "studentId": "STU-0001", "ym": YM, "method": "cash", "keys": ["TCH-0001"], "lessonIds": [lesson_id]})
    assert status == 200 and r["amount"] == 2000                    # ровно за выбранное занятие
    pending = [p for p in dp["payment_repo"].rows if p.status != PaymentStatus.PAID]
    assert pending and pending[0].lesson_ids == lesson_id           # намерение записано в остаток


def test_bank_details_are_returned_without_bot(api, monkeypatch):
    app, dp = api
    monkeypatch.setattr(settings, "payment_bank_details", "Банк: Тинькофф\\nПолучатель: Школа")
    status, r = _call(app, "POST", "/api/parent/pay",
                      json={"studentId": "STU-0001", "ym": YM, "method": "bank"})
    assert status == 200 and "Тинькофф" in r["details"] and "\n" in r["details"] and r["amount"] == 4800
    assert _call(app, "POST", "/api/parent/pay",
                 json={"studentId": "STU-0001", "ym": YM, "method": "wire"})[0] == 400


def test_bank_details_include_qr(api, monkeypatch):
    app, dp = api
    monkeypatch.setattr(settings, "payment_bank_details", "Банк: Тинькофф")
    monkeypatch.setattr(settings, "payment_qr_data", "ST00012|Name=Школа|PersonalAcc=40817810000000000001")
    status, r = _call(app, "POST", "/api/parent/pay",
                      json={"studentId": "STU-0001", "ym": YM, "method": "bank"})
    assert status == 200 and r["qr"].startswith("data:image/png;base64,")


def _sub_mode(dp, price=6000):
    """Группу ученика переводим на абонемент — в счёте появляется позиция SUB:GRP-0001."""
    group = dp["group_repo"].items[0]
    group.billing_mode = GroupBillingMode.SUBSCRIPTION
    group.price_full = price


def test_parent_pays_only_the_subscription(api):
    app, dp = api
    _sub_mode(dp)
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/parent/pay", bot=bot, json={
        "studentId": "STU-0001", "ym": YM, "method": "cash", "keys": ["SUB:GRP-0001"]})
    assert status == 200 and r["amount"] == 6000              # ровно абонемент, без занятий педагога


def test_parent_mixes_subscription_and_one_lesson(api):
    app, dp = api
    _sub_mode(dp)
    status, d = _call(app, "GET", f"/api/parent/bill/STU-0001/{YM}")
    teacher = next(r for r in d["rows"] if r["key"] == "TCH-0001")
    lesson = next(x for x in teacher["lessons"] if not x["paid"])
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/parent/pay", bot=bot, json={
        "studentId": "STU-0001", "ym": YM, "method": "cash",
        "keys": ["SUB:GRP-0001", "TCH-0001"], "lessonIds": [lesson["id"]]})
    assert status == 200 and r["amount"] == 6000 + lesson["amount"]
    pending = [p for p in dp["payment_repo"].rows
               if p.teacher_id == "TCH-0001" and p.status != PaymentStatus.PAID]
    assert pending and pending[0].lesson_ids == lesson["id"]   # намерение — только отмеченное занятие


def test_cash_is_marked_preferred_for_sport_groups(api, monkeypatch):
    """В спортивных группах школа просит наличные — кабинет помечает ребёнка флагом."""
    app, dp = api
    monkeypatch.setattr(settings, "cash_preferred_group_ids", "GRP-0001")
    assert _call(app, "GET", "/api/parent/me")[1]["children"][0]["cashPreferred"] is True
    monkeypatch.setattr(settings, "cash_preferred_group_ids", "GRP-9999")
    assert _call(app, "GET", "/api/parent/me")[1]["children"][0]["cashPreferred"] is False


def test_cash_can_be_switched_off_for_a_group(api, monkeypatch):
    """В части групп наличные не принимают — способ не показывается родителю."""
    app, dp = api
    monkeypatch.setattr(settings, "cash_disabled_group_ids", "GRP-0001")
    child = _call(app, "GET", "/api/parent/me")[1]["children"][0]
    assert child["cashAllowed"] is False and child["cashPreferred"] is False
    bot = FakeBot()
    assert _call(app, "POST", "/api/parent/pay", bot=bot,
                 json={"studentId": "STU-0001", "ym": YM, "method": "cash"})[0] == 200  # API не запрещаем
    monkeypatch.setattr(settings, "cash_disabled_group_ids", "")
    assert _call(app, "GET", "/api/parent/me")[1]["children"][0]["cashAllowed"] is True


def test_lessons_tab_has_no_money(api, monkeypatch):
    """Вкладка «Занятия» — расписание: ни сумм, ни статусов оплаты."""
    app, _dp = api
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    les = _call(app, "GET", f"/api/parent/lessons/STU-0001?ym={YM}")[1]
    assert les["lessons"] and not any(k in les["lessons"][0] for k in ("amount", "paid", "directAmount"))
    assert not any(k in les for k in ("rest", "unpaid", "directTotal"))
    assert les["lessons"][0]["teacherId"]                  # по нему фильтруют чипы педагогов


def test_direct_pay_teacher_is_shown_in_the_bill_but_not_charged(api, monkeypatch):
    """Педагог с прямой оплатой виден в счёте как все, но в «К оплате» не входит."""
    app, _dp = api
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    bill = _call(app, "GET", f"/api/parent/bill/STU-0001/{YM}")[1]
    direct = [r for r in bill["rows"] if r.get("direct")]
    assert len(direct) == 1 and direct[0]["name"] == "Река Станислав"
    assert direct[0]["accrued"] > 0 and direct[0]["rest"] == 0
    assert all(x["type"] == "individual" for x in direct[0]["lessons"])
    assert bill["accrued"] == sum(r["accrued"] for r in bill["rows"] if not r.get("direct"))


def test_bills_start_from_the_configured_month(api, monkeypatch):
    """Месяцы до PARENT_BILLS_SINCE_PERIOD родителю не показываем — это архив школы."""
    app, _dp = api
    monkeypatch.setattr(settings, "parent_bills_since_period", "")
    all_months = _call(app, "GET", "/api/parent/bills")[1]["months"]
    monkeypatch.setattr(settings, "parent_bills_since_period", YM)
    from_sep = _call(app, "GET", "/api/parent/bills")[1]["months"]
    assert [m["ym"] for m in from_sep] == [YM] and all(m["ym"] >= YM for m in from_sep)
    assert len(from_sep) <= len(all_months)
    monkeypatch.setattr(settings, "parent_bills_since_period", "2099-01")
    assert _call(app, "GET", "/api/parent/bills")[1]["months"] == []
