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


def test_home_attention_lessons_and_month_blocks(api):
    """Сводка «Мои дети»: что оплатить, что ждёт подтверждения, новые оценки; занятия месяца; месяц по ребёнку."""
    from datetime import date as _date
    from tests.test_admin_inbox_api import PendingRepoFake
    app, dp = api
    today = _date.today().isoformat()
    dp["student_repo"].items[0].athlete_tg_id = 777001                 # Иванов ведёт дневник
    fresh = mk_entry("TE-NEW", "STU-0001", today, 45, topics=["Самба"], grade=5)
    fresh.graded_by, fresh.grade_comment = "TCH-0001", "Молодец"
    dp["entry_repo"].items.append(fresh)
    dp["pending_repo"] = PendingRepoFake()
    asyncio.run(dp["pending_repo"].add("cash", "STU-0001", "Иванов Иван", YM, amount=4800, method="cash"))

    h = _call(app, "GET", "/api/parent/home")[1]
    c = h["children"][0]
    assert h["today"] == today and c["unpaid"] == [{"ym": YM, "accrued": 4800, "paid": 0, "rest": 4800}]
    assert c["pending"] == [{"kind": "cash", "amount": 4800, "ym": YM, "method": "cash"}]
    # на «сегодня» может попасть и старая запись фикстуры (5-го числа) — ищем именно свежую оценку
    fresh_grade = next(g for g in c["grades"] if g["date"] == today and g["comment"] == "Молодец")
    assert fresh_grade == {"date": today, "grade": 5, "topics": ["Самба"], "teacher": "Река Станислав", "comment": "Молодец"}
    lessons = c["lessons"]
    assert (lessons["month"], lessons["minutes"], lessons["last"]) == (3, 150, f"{YM}-12")
    assert all(x["teacher"] == "Река Станислав" and x["durationMin"] in (45, 60) for x in lessons["today"])


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


def test_nothing_before_the_start_month_anywhere_in_the_cabinet(api, monkeypatch):
    """Кабинет родителя работает с сентября: занятия, дневник и счёт за ранние месяцы не отдаём."""
    from bot.utils.dates import last_periods
    app, _dp = api
    prev = last_periods(2)[1]
    monkeypatch.setattr(settings, "parent_bills_since_period", YM)
    assert _call(app, "GET", "/api/parent/me")[1]["historySince"] == YM
    assert _call(app, "GET", f"/api/parent/lessons/STU-0001?ym={prev}")[1]["lessons"] == []
    d = _call(app, "GET", f"/api/parent/diary/STU-0001?ym={prev}")[1]
    assert d["entries"] == [] and d["stats"]["sessions"] == 0
    assert _call(app, "GET", f"/api/parent/bill/STU-0001/{prev}")[0] == 404
    assert _call(app, "GET", f"/api/parent/lessons/STU-0001?ym={YM}")[1]["lessons"]   # текущий месяц — как обычно
    monkeypatch.setattr(settings, "parent_bills_since_period", "")
    assert _call(app, "GET", f"/api/parent/bill/STU-0001/{prev}")[0] == 200         # без границы — всё видно


def test_receipt_upload_from_the_cabinet(api, monkeypatch):
    """Чек по реквизитам из кабинета: файл уходит админам с кнопками очереди, заявка хранит file_id."""
    from types import SimpleNamespace
    from tests.test_admin_inbox_api import PendingRepoFake
    import aiohttp
    app, dp = api
    dp["pending_repo"] = PendingRepoFake()
    monkeypatch.setattr(settings, "payment_bank_details", "Банк")

    class Bot:
        def __init__(self):
            self.photos, self.markups = [], []

        async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_):
            self.photos.append((chat_id, caption, reply_markup))
            return SimpleNamespace(message_id=len(self.photos), photo=[SimpleNamespace(file_id="FILE-CAB")])

        async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **_):
            self.markups.append((chat_id, message_id, reply_markup))

    bot = Bot()

    async def run():
        app_ = web.Application(client_max_size=20 * 1024 ** 2)
        register_parent_api(app_, dp, bot)
        client = TestClient(TestServer(app_))
        await client.start_server()
        try:
            form = aiohttp.FormData()
            for name, value in (("studentId", "STU-0001"), ("ym", YM), ("method", "bank")):
                form.add_field(name, value)
            form.add_field("amount", "4800")
            form.add_field("file", b"\x89PNG fake", filename="receipt.png", content_type="image/png")
            resp = await client.post("/api/parent/receipt", data=form,
                                     headers={"Authorization": f"tma {make_init_data(user_id=PARENT_TG)}"})
            return resp.status, await resp.json()
        finally:
            await client.close()

    status, r = asyncio.run(run())
    assert status == 200 and r["ok"] and r["amount"] == 4800 and r["notified"] == 1
    a = dp["pending_repo"].items[0]
    assert (a.kind, a.student_id, a.period_month, a.amount, a.method, a.file_id, a.file_type) == (
        "receipt", "STU-0001", YM, 4800, "bank", "FILE-CAB", "photo")
    assert a.parent_addr == str(PARENT_TG)
    chat, caption, first_markup = bot.photos[0]
    assert chat == ADMIN_TG and "Чек об оплате" in caption and "Иванов Иван" in caption and first_markup is None
    callbacks = [b.callback_data for row in bot.markups[0][2].inline_keyboard for b in row]
    assert any(c.startswith(f"pact:{a.action_id}:4800:") for c in callbacks)   # кнопки — по номеру решения


def _with_previous_month_lesson(dp, monkeypatch):
    """Занятие прошлого месяца: счёт за него остаётся неоплаченным, пока родитель не заплатит."""
    from datetime import date

    from dateutil.relativedelta import relativedelta

    from bot.utils.dates import last_periods
    from tests.fakes import mk_lesson
    prev = last_periods(2)[1]
    monkeypatch.setattr(settings, "parent_bills_since_period", "")
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    lesson_date = (date.today().replace(day=1) - relativedelta(days=2)).isoformat()
    assert lesson_date[:7] == prev
    dp["lesson_repo"].items += [mk_lesson("LES-P1", teacher, lesson_date, students=[("STU-0001", "Иванов Иван")]),
                                mk_lesson("LES-P2", teacher, lesson_date[:8] + "10", students=[("STU-0001", "Иванов Иван")])]
    return prev


def test_single_lesson_can_be_paid_in_previous_and_current_month(api, monkeypatch):
    """Оплата отдельных занятий работает и за прошлый месяц, и за текущий — выбранные занятия, не весь остаток."""
    app, dp = api
    prev = _with_previous_month_lesson(dp, monkeypatch)
    for ym in (prev, YM):
        status, d = _call(app, "GET", f"/api/parent/bill/STU-0001/{ym}")
        assert status == 200
        lesson = next(x for x in d["rows"][0]["lessons"] if not x["paid"])
        status, r = _call(app, "POST", "/api/parent/pay", bot=FakeBot(), json={
            "studentId": "STU-0001", "ym": ym, "method": "cash", "keys": [d["rows"][0]["key"]], "lessonIds": [lesson["id"]]})
        assert status == 200 and r["amount"] == lesson["amount"] < d["rows"][0]["rest"], ym      # ровно за занятие
    pending = {(p.period_month, p.lesson_ids) for p in dp["payment_repo"].rows if p.status != PaymentStatus.PAID}
    assert any(pm == prev and ids for pm, ids in pending) and any(pm == YM and ids for pm, ids in pending)


def test_pay_all_months_by_cash_creates_a_request_per_month(api, monkeypatch):
    """«Оплатить всё»: наличные за все месяцы с остатком — заявка и сообщение админу на каждый месяц."""
    from tests.test_admin_inbox_api import PendingRepoFake
    app, dp = api
    prev = _with_previous_month_lesson(dp, monkeypatch)
    dp["pending_repo"] = PendingRepoFake()
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/parent/pay", bot=bot,
                      json={"studentId": "STU-0001", "ym": YM, "method": "cash", "periods": [prev, YM]})
    assert status == 200 and r["months"] == 2 and r["notified"] == 2
    items = dp["pending_repo"].items
    assert sorted(a.period_month for a in items) == sorted([prev, YM])
    assert r["amount"] == sum(a.amount for a in items) and len(bot.sent) == 2
    # повторное нажатие новых заявок не создаёт
    status, again = _call(app, "POST", "/api/parent/pay", bot=bot,
                          json={"studentId": "STU-0001", "ym": YM, "method": "cash", "periods": [prev, YM]})
    assert status == 200 and again["duplicate"] and len(dp["pending_repo"].items) == 2


def test_pay_all_months_by_bank_returns_total_with_one_purpose(api, monkeypatch):
    app, dp = api
    prev = _with_previous_month_lesson(dp, monkeypatch)
    monkeypatch.setattr(settings, "payment_bank_details", "Банк\\nсчёт 1")
    status, r = _call(app, "POST", "/api/parent/pay",
                      json={"studentId": "STU-0001", "ym": YM, "method": "bank", "periods": [prev, YM]})
    assert status == 200 and r["months"] == 2 and r["periods"] == sorted([prev, YM])
    assert "Оплата занятий, Иванов Иван," in r["details"] and prev[:4] in r["details"]


def test_parent_unlinks_a_wrong_child(api):
    """«Это не мой ребёнок»: родитель снимает свою привязку, админам — сообщение; чужой ребёнок — 404."""
    app, dp = api
    assert _call(app, "DELETE", "/api/parent/children/STU-0002")[0] == 404
    bot = FakeBot()
    status, r = _call(app, "DELETE", "/api/parent/children/STU-0001", bot=bot)
    assert status == 200 and r["left"] == 0
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0001")).parent_addrs == []
    assert bot.sent and "отвязался от ученика" in bot.sent[0][1] and "Иванов Иван" in bot.sent[0][1]
    assert _call(app, "GET", "/api/parent/me")[0] == 403                         # детей больше нет — кабинет закрыт
