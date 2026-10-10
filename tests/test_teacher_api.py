"""API кабинета педагога (/api/teacher/*): роль, свои занятия, группы, статистика, сдача периода."""
import asyncio
from datetime import date

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_teacher_api
from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services.diary_service import DiaryService
from config.settings import settings
from tests.fakes import (
    AthleteTaskRepoFake, FakeBot, TrainingEntryRepoFake,
    mk_entry, mk_lesson, mk_payment, mk_student, mk_submission, mk_user,
)
from tests.test_admin_api import ADMIN_TG, PARENT_TG, YM, NotifierFake, make_api
from tests.test_telegram_auth import make_init_data

TEACHER_TG, ATHLETE_TG = 4242, 777001


def _call(dp, method, path, tg_id=TEACHER_TG, json=None, headers=None, bot=None):
    async def run():
        app = web.Application()
        register_teacher_api(app, dp, bot)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            h = headers if headers is not None else {"Authorization": f"tma {make_init_data(user_id=tg_id)}"}
            resp = await client.request(method, path, headers=h, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.fixture(autouse=True)
def _receipt_rc():
    """Чек перевода, уже прикреплённый педагогом TCH-0001 к ученику STU-0001 (отметка перевода без чека — 400)."""
    import time

    from bot.api import teacher as teacher_api
    teacher_api._teacher_receipts["RC"] = ("TCH-0001", "STU-0001", time.time(), "")
    yield
    teacher_api._teacher_receipts.clear()


@pytest.fixture()
def api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    # замки и сдача проверяются при включённом флаге; отдельный тест выключает его
    monkeypatch.setattr(settings, "teacher_period_submit_enabled", True)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))   # педагог со своим кабинетом
    dp["student_repo"].items[0].athlete_tg_id = ATHLETE_TG                # Иванов — спортсмен с кабинетом
    dp["entry_repo"] = TrainingEntryRepoFake([
        mk_entry("TE-1", "STU-0001", f"{YM}-05", 60, topics=["Джайв"]),
        mk_entry("TE-2", "STU-0001", f"{YM}-07", 30, topics=["Самба"], grade=5),
    ])
    dp["task_repo"] = AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    dp["notifier"] = NotifierFake()
    return dp, dp


def test_only_linked_teacher_gets_in(api):
    app, dp = api
    status, me = _call(app, "GET", "/api/teacher/me")
    assert status == 200 and (me["teacherId"], me["name"]) == ("TCH-0001", "Река Станислав")
    assert _call(app, "GET", "/api/teacher/me", tg_id=PARENT_TG)[0] == 403        # родитель — не педагог
    assert _call(app, "GET", "/api/teacher/me", tg_id=ADMIN_TG)[0] == 403         # админ без teacher_id
    assert _call(app, "GET", "/api/teacher/me", headers={})[0] == 401


def test_home_attention_today_and_month_blocks(api, monkeypatch):
    """Сводка педагога: записи без оценки, занятия дня целиком, счётчики месяца; счета — только у BILLING_TEACHER_IDS."""
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    today = date.today().isoformat()
    dp["lesson_repo"].items.append(mk_lesson("LES-TODAY", teacher, today, students=[("STU-0002", "Петрова Анна")]))
    h = _call(app, "GET", "/api/teacher/home")[1]
    assert h["unrated"] == 1 and h["bills"] is None                    # TE-1 без оценки; счетов у обычного педагога нет
    assert h["lessonsToday"] == 1 and [x["id"] for x in h["todayLessons"]] == ["LES-TODAY"]
    assert h["todayLessons"][0]["students"] == ["Петрова Анна"] and h["todayLessons"][0]["earned"] == 1500
    assert (h["lessonsMonth"], h["groupLessonsMonth"], h["individualLessonsMonth"]) == (4, 1, 3)

    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    dp["payment_repo"].rows.append(mk_payment("PAY-1", "STU-0001", YM, "TCH-0001", 4800, status=PaymentStatus.PAID))
    h = _call(app, "GET", "/api/teacher/home")[1]
    # Иванов оплатил всё (2000 + 2000 + 800); Петрова должна 800 за группу и 2000 за сегодняшний урок
    assert h["bills"] == {"students": 1, "rest": 2800}


def test_home_and_lessons_are_own_only(api):
    app, dp = api
    status, h = _call(app, "GET", "/api/teacher/home")
    assert status == 200 and h["lessonsMonth"] == 3 and h["earnedMonth"] == 4333
    # в данных сдан только август: «прошлый месяц сдан» — только пока прошлый месяц и есть август
    assert h["prevSubmitted"] is (h["prevPeriod"] == "2026-08") and h["periodSubmitted"] is False

    status, d = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")
    assert status == 200 and [x["id"] for x in d["lessons"]] == ["LES-1", "LES-2", "LES-3"]
    assert d["lessons"][2]["students"] == ["Иванов Иван", "Петрова Анна"] and d["lessons"][2]["groupName"] == "БП Джаз"
    assert d["earned"] == 4333 and all(x["locked"] is False for x in d["lessons"])

    status, one = _call(app, "GET", "/api/teacher/lessons/LES-3")
    assert status == 200 and one["earned"] == 1333 and [a["amount"] for a in one["attendees"]] == [800, 800]
    dp["lesson_repo"].items.append(mk_lesson("LES-OTHER", dp["teacher_repo"].items[0], f"{YM}-05"))
    dp["lesson_repo"].items[-1].teacher_id = "TCH-0099"                        # чужое занятие не видно
    assert _call(app, "GET", "/api/teacher/lessons/LES-OTHER")[0] == 404
    assert _call(app, "DELETE", "/api/teacher/lessons/LES-OTHER")[0] == 404


def test_lesson_card_shows_payment_status_per_student(api):
    """Карточка занятия у педагога: у каждого ученика — оплачено / не оплачено по оплатам месяца."""
    app, dp = api
    one = _call(app, "GET", "/api/teacher/lessons/LES-3")[1]
    assert [a["payStatus"] for a in one["attendees"]] == ["unpaid", "unpaid"]
    assert (one["paidCount"], one["payableCount"]) == (0, 2)
    dp["payment_repo"].rows.append(mk_payment("PAY-1", "STU-0002", YM, "TCH-0001", 800, status=PaymentStatus.PAID))
    one = _call(app, "GET", "/api/teacher/lessons/LES-3")[1]
    assert {a["studentId"]: a["payStatus"] for a in one["attendees"]} == {"STU-0001": "unpaid", "STU-0002": "paid"}
    assert (one["paidCount"], one["payableCount"]) == (1, 2)
    solo = _call(app, "GET", "/api/teacher/lessons/LES-1")[1]          # индивидуальное: один ученик
    assert solo["attendees"][0]["payStatus"] == "unpaid" and solo["payableCount"] == 1
    # журнал: та же отметка в каждой строке
    rows = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")[1]["lessons"]
    assert {r["id"]: (r["paidCount"], r["payableCount"]) for r in rows} == {
        "LES-1": (0, 1), "LES-2": (0, 1), "LES-3": (1, 2)}


def test_lesson_card_status_for_subscription_and_direct_pay(api, monkeypatch):
    from tests.test_parent_api import _sub_mode
    app, dp = api
    _sub_mode(dp, price=6000)                                            # БП Джаз — абонемент 6000/мес
    teacher = dp["teacher_repo"].items[0]
    # абонементное занятие пишется без посещаемости: в карточке — состав группы с оплатой абонемента
    dp["lesson_repo"].items.append(mk_lesson("LES-SUB", teacher, f"{YM}-14", duration=60,
                                             lesson_type=LessonType.GROUP, group_id="GRP-0001"))
    one = _call(app, "GET", "/api/teacher/lessons/LES-SUB")[1]
    assert one["roster"] and [a["payStatus"] for a in one["attendees"]] == ["sub_unpaid", "sub_unpaid"]
    dp["payment_repo"].rows.append(mk_payment("PAY-S", "STU-0001", YM, "SUB:GRP-0001", 6000, status=PaymentStatus.PAID))
    one = _call(app, "GET", "/api/teacher/lessons/LES-SUB")[1]
    assert {a["studentId"]: a["payStatus"] for a in one["attendees"]} == {"STU-0001": "sub_paid", "STU-0002": "sub_unpaid"}
    assert (one["paidCount"], one["payableCount"]) == (1, 2)
    # прямая оплата: школа не отслеживает — статуса нет
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    solo = _call(app, "GET", "/api/teacher/lessons/LES-1")[1]
    assert solo["attendees"][0]["payStatus"] is None and solo["payableCount"] == 0


def test_period_lock_blocks_delete_and_record(api):
    app, dp = api
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-AUG", teacher, "2026-08-20", students=[("STU-0001", "Иванов Иван")]))
    status, err = _call(app, "DELETE", "/api/teacher/lessons/LES-AUG")         # август сдан
    assert status == 409 and err["error"] == "period_locked"
    body = {"kind": "soloist", "date": "2026-08-21", "durationMin": 45, "studentIds": ["STU-0001"]}
    assert _call(app, "POST", "/api/teacher/record", json=body)[0] == 409
    # свой открытый месяц: запись и удаление работают
    status, r = _call(app, "POST", "/api/teacher/record",
                      json={"kind": "soloist", "date": date.today().isoformat(),   # не в будущем: 1-го числа «YM-14» ещё не наступило
                            "durationMin": 45, "studentIds": ["STU-0002"]})
    assert status == 200 and r["created"] == 1
    assert _call(app, "DELETE", f"/api/teacher/lessons/{r['lessons'][0]}")[1]["ok"] is True


def test_record_options_and_groups_are_scoped_to_teacher(api):
    app, dp = api
    status, o = _call(app, "GET", "/api/teacher/record/options")
    assert status == 200 and o["teacherId"] == "TCH-0001" and [g["id"] for g in o["groups"]] == ["GRP-0001"]

    status, g = _call(app, "GET", "/api/teacher/groups")
    assert status == 200 and [(x["name"], x["students"]) for x in g["groups"]] == [("БП Джаз", 2)]
    status, card = _call(app, "GET", "/api/teacher/groups/GRP-0001")
    assert status == 200 and [s["name"] for s in card["students"]] == ["Иванов Иван", "Петрова Анна"]
    assert _call(app, "GET", "/api/teacher/groups/GRP-0404")[0] == 404

    status, s = _call(app, "GET", f"/api/teacher/students/STU-0001?ym={YM}")
    assert status == 200 and s["name"] == "Иванов Иван" and [x["id"] for x in s["lessons"]] == ["LES-1", "LES-2", "LES-3"]


def test_stats_and_submit_period(api):
    app, dp = api
    status, st = _call(app, "GET", f"/api/teacher/stats?ym={YM}")
    assert status == 200 and st["total"] == 4333 and (st["groupLessons"], st["individualLessons"]) == (1, 2)
    # у строк-занятий свой label пустой — подставляем, кто занимался
    assert [ln["label"] for ln in st["lines"]] == ["Иванов Иван", "Иванов Иван", "БП Джаз"]

    status, pre = _call(app, "GET", f"/api/teacher/submit?ym={YM}")
    assert status == 200 and pre["lessons"] == 3 and pre["submitted"] is False
    if date.today().day < 25:                                   # правило «сдать с 25-го»
        assert pre["canSubmit"] is False
        assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[0] == 409
        return
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[1]["ok"] is True
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": YM})[0] == 409     # повторно — нельзя


def test_submit_rejects_already_submitted_month(api):
    app, dp = api
    dp["submission_repo"].items.append(mk_submission("TCH-0001", "2026-07"))
    status, err = _call(app, "POST", "/api/teacher/submit", json={"ym": "2026-07"})
    assert status == 409 and err["error"] == "already"
    assert _call(app, "POST", "/api/teacher/submit", json={"ym": "bad"})[0] == 400


def test_lesson_types_are_reported(api):
    app, dp = api
    status, d = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")
    kinds = {x["id"]: x["type"] for x in d["lessons"]}
    assert kinds == {"LES-1": LessonType.INDIVIDUAL.value, "LES-2": LessonType.INDIVIDUAL.value,
                     "LES-3": LessonType.GROUP.value}


def test_unknown_user_is_forbidden(api):
    app, dp = api
    assert _call(app, "GET", "/api/teacher/home", tg_id=999999)[0] == 403
    dp["user_repo"].items.append(mk_user(777, teacher_id="TCH-0404"))    # ссылка на несуществующего педагога
    assert _call(app, "GET", "/api/teacher/home", tg_id=777)[0] == 403


def test_diary_lists_own_athletes_and_grades_entry(api):
    app, dp = api
    status, d = _call(app, "GET", "/api/teacher/diary")
    assert status == 200 and [(a["name"], a["unrated"]) for a in d["athletes"]] == [("Иванов Иван", 1)]

    status, card = _call(app, "GET", f"/api/teacher/diary/STU-0001?ym={YM}")
    assert status == 200 and card["stats"]["sessions"] == 2 and card["stats"]["minutes"] == 90
    assert [e["id"] for e in card["entries"]] == ["TE-2", "TE-1"]      # новые сверху

    bot = FakeBot()
    status, r = _call(app, "POST", "/api/teacher/diary/entries/TE-1/grade", json={"grade": 4, "comment": "Ровнее корпус"}, bot=bot)
    assert status == 200 and r["grade"] == 4
    assert dp["entry_repo"].items[0].grade == 4 and dp["entry_repo"].items[0].graded_by == "TCH-0001"
    assert bot.sent and str(ATHLETE_TG) in str(bot.sent[0])            # спортсмену ушёл пуш
    assert _call(app, "POST", "/api/teacher/diary/entries/TE-1/grade", json={"grade": 9})[0] == 400
    assert _call(app, "POST", "/api/teacher/diary/entries/TE-404/grade", json={"grade": 4})[0] == 404


def test_diary_tasks_flow(api):
    app, dp = api
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/teacher/diary/STU-0001/tasks",
                      json={"exercise": "Махи у станка", "minutes": 15, "comment": "каждый день"}, bot=bot)
    assert status == 200 and r["ok"] is True and bot.sent
    status, t = _call(app, "GET", "/api/teacher/diary/STU-0001/tasks")
    assert status == 200 and [(x["exercise"], x["status"]) for x in t["tasks"]] == [("Махи у станка", "open")]

    tid = t["tasks"][0]["id"]
    assert _call(app, "POST", f"/api/teacher/diary/tasks/{tid}/close")[1]["ok"] is True
    assert dp["task_repo"].items[0].status == "closed"
    assert _call(app, "POST", "/api/teacher/diary/STU-0002/tasks", json={"exercise": "x", "minutes": 5})[0] == 404
    assert _call(app, "POST", "/api/teacher/diary/STU-0001/tasks", json={"exercise": "", "minutes": 5})[0] == 400


def test_diary_rating_marks_own_athletes(api):
    app, dp = api
    status, r = _call(app, "GET", f"/api/teacher/diary/rating?ym={YM}")
    assert status == 200 and [(x["name"], x["mine"]) for x in r["rows"]] == [("Иванов Иван", True)]


def test_bills_only_for_billing_teachers(api, monkeypatch):
    app, dp = api
    assert _call(app, "GET", "/api/teacher/bills")[0] == 403          # право не выдано
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")

    status, d = _call(app, "GET", f"/api/teacher/bills?ym={YM}")
    assert status == 200 and [g["name"] for g in d["groups"]] == ["БП Джаз"]

    status, g = _call(app, "GET", f"/api/teacher/bills/group/GRP-0001?ym={YM}")
    assert status == 200 and [(s["name"], s["total"]) for s in g["students"]] == [("Иванов Иван", 4800), ("Петрова Анна", 800)]

    status, b = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")
    assert status == 200 and b["total"] == 4800 and b["rest"] == 4800
    assert _call(app, "GET", "/api/teacher/bills/group/GRP-0404")[0] == 404


def test_bills_send_needs_bot(api, monkeypatch):
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    assert _call(app, "POST", f"/api/teacher/bills/student/STU-0001/send?ym={YM}")[0] == 503   # бот не передан
    status, r = _call(app, "POST", f"/api/teacher/bills/student/STU-0001/send?ym={YM}", bot=FakeBot())
    assert status == 200 and r["recipients"] >= 1                     # у Иванова привязан родитель


def test_billing_teacher_marks_payment_in_own_group(api, monkeypatch):
    app, dp = api
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0001", "amount": 1000})[0] == 403   # без права счетов
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")

    status, m = _call(app, "GET", f"/api/teacher/bills/student/STU-0001/marks?ym={YM}&key=TCH-0001")
    assert status == 200 and [x["paid"] for x in m["marks"]] == [False, False, False]
    assert m["ledger"]["remainder"] == 4800

    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "receipt_bank", "receiptId": "RC"})
    assert status == 200 and r["credited"] == 2000
    assert [p.total_amount for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID] == [2000]

    status, m2 = _call(app, "GET", f"/api/teacher/bills/student/STU-0001/marks?ym={YM}&key=TCH-0001")
    assert m2["ledger"]["paid"] == 2000 and [x["paid"] for x in m2["marks"]] == [True, False, False]
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0001", "amount": 0})[0] == 400
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0001", "amount": 100, "method": "yookassa"})[0] == 400
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",   # наличные — только через админа
                      json={"ym": YM, "key": "TCH-0001", "amount": 100, "method": "cash"})
    assert status == 400 and r["error"] == "cash_via_admin"


def test_billing_teacher_cannot_overpay_silently(api, monkeypatch):
    app, _dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    body = {"ym": YM, "key": "TCH-0001", "amount": 9000, "method": "receipt_bank", "receiptId": "RC"}
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json=body)
    assert status == 409 and r["needsConfirm"] and r["rest"] == 4800
    # переплату педагог не зачитывает даже с подтверждением (решение владельца 10.10.2026) — только остаток
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json={**body, "force": True})
    assert status == 409 and r["allowOverpay"] is False and r["rest"] == 4800
    assert not [p for p in _dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json={**body, "amount": 4800})
    assert status == 200 and r["credited"] == 4800 and r["overpaid"] == 0
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json={**body, "amount": 100, "force": True})
    assert status == 409 and r["rest"] == 0                                        # уже оплачено — второй раз нельзя


def test_direct_pay_lessons_show_parent_amount_not_zero(api, monkeypatch):
    """Занятия с прямой оплатой: школа начисляет 0, но педагог видит сумму родителя и аренду."""
    app, _dp = api
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "hall_rent_per_lesson", "TCH-0001:500")
    monkeypatch.setattr(settings, "hall_rent_since_period", "")

    lessons = _call(app, "GET", f"/api/teacher/lessons?ym={YM}")[1]["lessons"]
    solo = [x for x in lessons if x["type"] == "individual"]
    assert solo and all(x["direct"] and x["earned"] == 0 for x in solo)
    assert all(x["directAmount"] > 0 and x["rent"] == 500 for x in solo)
    assert [x["direct"] for x in lessons if x["type"] == "group"] == [False]   # группы — как обычно

    card = _call(app, "GET", f"/api/teacher/lessons/{solo[0]['id']}")[1]
    assert card["direct"] and card["directAmount"] == solo[0]["directAmount"] and card["rent"] == 500
    assert card["attendees"][0]["amount"] > 0                    # доля ученика, а не пусто

    home = _call(app, "GET", "/api/teacher/home")[1]
    assert home["directMonth"] == sum(x["directAmount"] for x in lessons)


def test_stats_direct_block_sums_by_student(api, monkeypatch):
    """Блок «Прямая оплата»: сколько должны родители по ученикам и сколько аренды школе."""
    app, _dp = api
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "hall_rent_per_lesson", "TCH-0001:500")
    monkeypatch.setattr(settings, "hall_rent_since_period", "")

    st = _call(app, "GET", f"/api/teacher/stats?ym={YM}")[1]
    d = st["direct"]
    assert d["lessons"] == 2 and d["rent"] == 1000 and d["rentPerLesson"] == 500
    assert d["total"] == sum(x["amount"] for x in d["students"]) > 0
    assert [x["name"] for x in d["students"]] == ["Иванов Иван"]
    # строки прямой оплаты помечены — кабинет сворачивает их в одну
    assert sum(1 for ln in st["lines"] if ln["direct"]) == 2
    assert all(ln["amount"] == 0 for ln in st["lines"] if ln["direct"])


def test_no_direct_block_for_ordinary_teacher(api):
    """У обычного педагога блока прямой оплаты нет — экран не меняется."""
    app, _dp = api
    st = _call(app, "GET", f"/api/teacher/stats?ym={YM}")[1]
    assert st["direct"] is None and not any(ln["direct"] for ln in st["lines"])
    assert all(x["direct"] is False and x["directAmount"] == 0
               for x in _call(app, "GET", f"/api/teacher/lessons?ym={YM}")[1]["lessons"])


def test_period_submission_can_be_switched_off(api, monkeypatch):
    """TEACHER_PERIOD_SUBMIT_ENABLED=false: кнопки нет, API отказывает, сводка без замка и напоминаний."""
    from bot.keyboards.teacher import kb_teacher_menu
    app, dp = api
    monkeypatch.setattr(settings, "teacher_period_submit_enabled", False)
    assert _call(app, "GET", "/api/teacher/me")[1]["periodSubmit"] is False
    home = _call(app, "GET", "/api/teacher/home")[1]
    assert home["periodSubmit"] is False and home["canSubmit"] is False
    status, err = _call(app, "POST", "/api/teacher/submit", json={"ym": YM})
    assert status == 403 and err["error"] == "disabled"
    assert not any(x.period_month == YM for x in dp["submission_repo"].items)   # текущий месяц не сдан
    labels = [b.text for row in kb_teacher_menu(teacher_id="TCH-0001").inline_keyboard for b in row]
    assert "📤 Сдать период" not in labels
    monkeypatch.setattr(settings, "teacher_period_submit_enabled", True)
    labels = [b.text for row in kb_teacher_menu(teacher_id="TCH-0001").inline_keyboard for b in row]
    assert "📤 Сдать период" in labels


def test_senior_teacher_switches_twice_a_week(api, monkeypatch):
    """Старший тренер (SENIOR_TEACHER_IDS) меняет «2 / 3 раза в неделю» ученику своей группы; обычный — нет."""
    from tests.test_parent_api import _sub_mode
    app, dp = api
    _sub_mode(dp, price=7000)
    monkeypatch.setattr(settings, "subscription_twice_prices", "GRP-0001:6000")
    body = {"groupId": "GRP-0001", "times": 2, "since": YM}
    assert _call(app, "GET", "/api/teacher/students/STU-0001")[1]["tariffs"] == []
    assert _call(app, "PUT", "/api/teacher/students/STU-0001/frequency", json=body)[0] == 403
    monkeypatch.setattr(settings, "senior_teacher_ids", "TCH-0001")
    card = _call(app, "GET", "/api/teacher/students/STU-0001")[1]
    assert card["tariffs"][0]["freq"]["times"] == 3
    assert _call(app, "PUT", "/api/teacher/students/STU-0001/frequency", json=body)[0] == 200
    assert _call(app, "GET", "/api/teacher/students/STU-0001")[1]["tariffs"][0]["freq"]["times"] == 2
    assert _call(app, "PUT", "/api/teacher/students/STU-0001/frequency", json={**body, "groupId": "GRP-0099"})[0] == 404


def test_teacher_bill_shows_only_own_directions(api, monkeypatch):
    """Педагог в счёте ученика видит только свои направления: занятия своих групп и свои индивидуальные.
    Индивидуальное у другого педагога (другое направление) не видно и в «к оплате» не входит."""
    from tests.fakes import mk_teacher
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    other = mk_teacher("TCH-0002", "Контарева Елизавета", rate_group=1000, rate_for_teacher=1500, rate_for_student=3000)
    dp["teacher_repo"].items.append(other)
    dp["lesson_repo"].items.append(mk_lesson("LES-BT", other, f"{YM}-20", students=[("STU-0001", "Иванов Иван")]))
    b = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    assert [r["key"] for r in b["rows"]] == ["TCH-0001"] and b["total"] == 4800     # 2000 + 2000 + 800, без 3000
    g = _call(app, "GET", f"/api/teacher/bills/group/GRP-0001?ym={YM}")[1]
    assert next(x for x in g["students"] if x["id"] == "STU-0001")["rest"] == 4800
    assert _call(app, "GET", f"/api/teacher/bills/student/STU-0001/marks?ym={YM}&key=TCH-0002")[0] == 404
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0002", "amount": 3000, "method": "receipt_bank", "receiptId": "RC"})[0] == 404


def test_full_bill_teacher_sees_other_teachers_and_gets_payment_notice(api, monkeypatch):
    """FULL_BILL_TEACHER_IDS (Контарева, 30.09.2026): полный счёт ученика своей группы, включая занятия
    у других педагогов, и уведомление в Telegram о каждой оплате такого ученика."""
    from bot.services import payment_events
    from tests.fakes import mk_teacher
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "TCH-0001")
    other = mk_teacher("TCH-0002", "Никишин Андрей", rate_group=1000, rate_for_teacher=1500, rate_for_student=3000)
    dp["teacher_repo"].items.append(other)
    dp["lesson_repo"].items.append(mk_lesson("LES-BT", other, f"{YM}-20", students=[("STU-0001", "Иванов Иван")]))
    b = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    assert sorted(r["key"] for r in b["rows"]) == ["TCH-0001", "TCH-0002"] and b["total"] == 4800 + 3000
    g = _call(app, "GET", f"/api/teacher/bills/group/GRP-0001?ym={YM}")[1]
    assert next(x for x in g["students"] if x["id"] == "STU-0001")["rest"] == 7800

    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))
    payment_events.setup(Bot(), dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"], dp["student_repo"],
                         dp["teacher_repo"])
    teacher_tg = next(u.tg_id for u in dp["user_repo"].items if u.teacher_id == "TCH-0001")

    async def pay_and_wait(actor):
        await dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 3000, actor, ["TCH-0002"],
                                                   payment_method="cash")
        await asyncio.gather(*payment_events._tasks)
    asyncio.run(pay_and_wait(555))                                  # отметил администратор
    assert sent and sent[0][0] == teacher_tg and "3000 ₽" in sent[0][1] and "Иванов Иван" in sent[0][1]
    sent.clear()
    asyncio.run(pay_and_wait(teacher_tg))                           # отметила сама — ей не дублируем,
    assert [c for c, _ in sent] == [ADMIN_TG]                       # а администратор узнаёт, что деньги у неё
    assert "деньги у педагога" in sent[0][1] and "Река Станислав" in sent[0][1]


def test_full_bill_only_for_listed_groups(api, monkeypatch):
    """FULL_BILL_GROUPS (Лобачева — ЮБ школа): полный счёт только у учеников перечисленных групп."""
    from tests.fakes import mk_teacher
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    other = mk_teacher("TCH-0002", "Контарева Елизавета", rate_group=1000, rate_for_teacher=1500, rate_for_student=3000)
    dp["teacher_repo"].items.append(other)
    dp["lesson_repo"].items.append(mk_lesson("LES-BT", other, f"{YM}-20", students=[("STU-0001", "Иванов Иван")]))
    url = f"/api/teacher/bills/student/STU-0001?ym={YM}"
    monkeypatch.setattr(settings, "full_bill_groups", "TCH-0001:GRP-0999")
    assert _call(app, "GET", url)[1]["total"] == 4800                        # не та группа — свои направления
    monkeypatch.setattr(settings, "full_bill_groups", "TCH-0001:GRP-0001")
    assert _call(app, "GET", url)[1]["total"] == 4800 + 3000                 # ученик группы из списка — полный счёт
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0002", "amount": 3000, "method": "receipt_bank", "receiptId": "RC"})[0] == 200


def test_payment_notice_only_for_listed_groups(api, monkeypatch):
    """FULL_BILL_GROUPS (Лобачева — ЮБ школа): уведомление только за учеников перечисленных групп."""
    from bot.services import payment_events
    app, dp = api
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(chat_id)
    payment_events.setup(Bot(), dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"], dp["student_repo"])

    async def pay():
        await dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 1000, 555, payment_method="cash")
        await asyncio.gather(*payment_events._tasks)
    monkeypatch.setattr(settings, "full_bill_groups", "TCH-0001:GRP-0999")
    asyncio.run(pay())
    assert sent == []                                              # ученик не в группе из списка
    monkeypatch.setattr(settings, "full_bill_groups", "TCH-0001:GRP-0001")
    asyncio.run(pay())
    assert len(sent) == 1


def test_revenue_share_individuals_stay_visible_to_their_teacher(api, monkeypatch):
    """Индивидуальные Яковлевой пишутся в служебную группу revenue-share: в её счёте они видны (регрессия 29.09)."""
    from tests.fakes import mk_group
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    dp["group_repo"].items.append(mk_group("GRP-0020", "ХГ Индивидуальные", billing_mode=GroupBillingMode.PER_VISIT, price_full=1800))
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0020"))
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-RS", teacher, f"{YM}-21", duration=60, lesson_type=LessonType.GROUP,
                                             attendees="STU-0001:60:1800", group_id="GRP-0020"))
    b = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    assert b["total"] == 4800 + 1800
    card = _call(app, "GET", "/api/teacher/students/STU-0001")[1]
    assert "ХГ Индивидуальные" not in card["groups"]               # служебная группа в карточке не светится


def test_admin_and_teacher_marking_same_lesson_at_once_credit_once(api, monkeypatch):
    """Админ и педагог одновременно отмечают оплату одного урока: зачтётся один раз, второй получит 409."""
    from bot.api import register_admin_api
    app_dp, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    lesson = next(x for x in dp["lesson_repo"].items if x.lesson_id == "LES-1")

    async def run():
        app = web.Application()
        register_admin_api(app, dp)
        register_teacher_api(app, dp)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            admin_h = {"Authorization": f"tma {make_init_data(user_id=ADMIN_TG)}"}
            teacher_h = {"Authorization": f"tma {make_init_data(user_id=TEACHER_TG)}"}
            a = client.post("/api/admin/pay/confirm", headers=admin_h, json={
                "studentId": "STU-0001", "periodMonth": YM, "key": "TCH-0001", "amount": 2000,
                "method": "cash", "lessonIds": [lesson.lesson_id]})
            t = client.post("/api/teacher/bills/student/STU-0001/pay", headers=teacher_h, json={
                "ym": YM, "key": "TCH-0001", "amount": 4800, "method": "receipt_bank", "receiptId": "RC"})
            ra, rt = await asyncio.gather(a, t)
            return ra.status, rt.status
        finally:
            await client.close()
    statuses = asyncio.run(run())
    paid = sum(p.total_amount for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID)
    assert sorted(statuses) == [200, 409] and paid in (2000, 4800)       # кто первый — тот и зачёл, без двойного


def test_full_bill_teacher_decides_parent_payment_requests(api, monkeypatch):
    """FULL_BILL_TEACHER_IDS (Контарева): «Ждут решения» — наличные и чеки учеников своих групп;
    решение то же, что у администратора: зачитывает оплату, закрывает заявку, повтор — 409."""
    from bot.repositories.pending_action_repo import DONE, KIND_CASH, KIND_CHILD, OPEN
    from tests.test_admin_inbox_api import PendingRepoFake
    app, dp = api
    dp["pending_repo"] = PendingRepoFake()
    own = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash", str(PARENT_TG)))
    asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-9999", "Чужой ученик", YM, 800, "cash"))
    asyncio.run(dp["pending_repo"].add(KIND_CHILD, "STU-0001", "Иванов Иван", "", 0, "", str(PARENT_TG)))
    assert _call(app, "GET", "/api/teacher/inbox")[0] == 403                     # без права — нет раздела
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "TCH-0001")
    assert _call(app, "GET", "/api/teacher/home")[1]["inbox"] == 1
    d = _call(app, "GET", "/api/teacher/inbox")[1]
    assert [a["id"] for a in d["items"]] == [own.action_id] and d["requests"] == []   # чужие и привязки не видны
    status, r = _call(app, "POST", f"/api/teacher/inbox/{own.action_id}/decide", json={"approve": True})
    # наличные педагог не зачитывает: «деньги у меня» → заявка ждёт администратора
    assert status == 200 and r["status"] == "held"
    assert dp["pending_repo"].items[0].status == OPEN and dp["pending_repo"].items[0].held_by == "TCH-0001"
    assert not [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert _call(app, "POST", f"/api/teacher/inbox/{own.action_id}/decide", json={"approve": True})[0] == 409
    assert _call(app, "GET", "/api/teacher/home")[1]["heldCash"] == 0             # счета выключены — без суммы
    item = _call(app, "GET", "/api/teacher/inbox")[1]["items"][0]
    assert item["note"] and item["approveLabel"] is None                          # у педагога — только пометка
    from tests.test_admin_inbox_api import _call as admin_call
    held = admin_call(dp, "GET", "/api/admin/inbox")[1]
    assert held["held"] == [{"name": "Река Станислав", "amount": 2000, "count": 1}]
    status, r = admin_call(dp, "POST", f"/api/admin/inbox/{own.action_id}/decide", json={"approve": True})
    assert status == 200 and r["credited"] == 2000 and dp["pending_repo"].items[0].status == DONE
    other = dp["pending_repo"].items[1].action_id
    assert _call(app, "POST", f"/api/teacher/inbox/{other}/decide", json={"approve": True})[0] == 404


def test_payment_request_copy_goes_to_full_bill_teacher_with_buttons(api, monkeypatch):
    """Новая заявка об оплате — педагогу из FULL_BILL_TEACHER_IDS копия с теми же кнопками pact:/pnay:;
    решать её он может только для учеников своих групп."""
    from bot.repositories.pending_action_repo import KIND_CASH
    from bot.services import payment_events
    from bot.services.pending_queue import queue_action
    from tests.test_admin_inbox_api import PendingRepoFake
    app, dp = api
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "TCH-0001")
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, reply_markup=None, **kw):
            sent.append((chat_id, text, [b.callback_data for row in reply_markup.inline_keyboard for b in row]))
    payment_events.setup(Bot(), dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"], dp["student_repo"])
    student = next(s for s in dp["student_repo"].items if s.student_id == "STU-0001")

    async def run():
        a = await queue_action(PendingRepoFake(), KIND_CASH, student, YM, amount=2000, method="cash")
        await asyncio.gather(*payment_events._tasks)
        return a
    action = asyncio.run(run())
    assert len(sent) == 1 and sent[0][0] == TEACHER_TG and "2000 ₽" in sent[0][1]
    assert sent[0][2] == [f"pact:{action.action_id}:2000:c", f"pnay:{action.action_id}"]
    teacher = asyncio.run(dp["user_repo"].get_by_tg_id(TEACHER_TG))
    assert asyncio.run(payment_events.may_decide(teacher, "STU-0001"))
    assert not asyncio.run(payment_events.may_decide(teacher, "STU-9999"))


def test_cash_scenarios_debt_until_admin_gets_money(api, monkeypatch):
    """Наличные (решение владельца 30.09.2026): педагог «приняла наличные» — это не оплата, а заявка
    администратору «деньги у педагога». Долг остаётся, напоминание не уходит; администратор зачитывает,
    когда получит деньги, — тогда долг закрывается и родителю приходит «оплата подтверждена»."""
    from bot.repositories.pending_action_repo import OPEN
    from bot.services import payment_events
    from tests.test_admin_inbox_api import PendingRepoFake, _call as admin_call
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    ps = dp["payment_service"]
    dp["pending_repo"] = PendingRepoFake()
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))
    payment_events.setup(Bot(), dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"],
                         dp["student_repo"], dp["teacher_repo"])
    before = asyncio.run(ps.compute_debt_map())["STU-0001"][YM]

    async def cash():                               # запрос и фоновые уведомления — в одном цикле событий
        web_app = web.Application()
        register_teacher_api(web_app, dp)
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            resp = await client.post("/api/teacher/bills/student/STU-0001/cash",
                                     headers={"Authorization": f"tma {make_init_data(user_id=TEACHER_TG)}"},
                                     json={"ym": YM, "parts": [{"key": "TCH-0001", "amount": 1000}]})
            await asyncio.gather(*payment_events._tasks)
            return resp.status, await resp.json()
        finally:
            await client.close()
    status, r = asyncio.run(cash())
    assert status == 200 and r["amount"] == 1000
    action = dp["pending_repo"].items[0]
    assert action.status == OPEN and action.held_by == "TCH-0001"
    assert asyncio.run(ps.compute_debt_map())["STU-0001"][YM] == before           # деньги не у школы — долг на месте
    assert _call(app, "GET", "/api/teacher/home")[1]["heldCash"] == 1000
    admin_msgs = [t for c, t in sent if c == ADMIN_TG]
    assert admin_msgs and "Наличные у педагога" in admin_msgs[0] and "Река Станислав" in admin_msgs[0]
    from bot.services.pending_queue import awaiting_periods
    assert ("STU-0001", YM) in asyncio.run(awaiting_periods(dp["pending_repo"]))   # напоминание не уйдёт

    status, r = admin_call(dp, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["credited"] == 1000
    assert asyncio.run(ps.compute_debt_map())["STU-0001"][YM] == before - 1000
    assert _call(app, "GET", "/api/teacher/home")[1]["heldCash"] == 0


def test_teacher_sees_student_lessons_in_own_directions(api, monkeypatch):
    """Карточка ученика у педагога → «Все занятия ученика»: свои направления (индивидуальное у другого
    педагога скрыто), с FULL_BILL — все; суммы и ✓ оплаты — только у педагога со счетами."""
    from tests.fakes import mk_teacher
    app, dp = api
    other = mk_teacher("TCH-0002", "Контарева Елизавета", rate_for_student=3000)
    dp["teacher_repo"].items.append(other)
    dp["lesson_repo"].items.append(mk_lesson("LES-BT", other, f"{YM}-20", students=[("STU-0001", "Иванов Иван")]))
    url = f"/api/teacher/students/STU-0001/lessons?ym={YM}"
    d = _call(app, "GET", url)[1]
    ids = [x["id"] for x in d["lessons"]]
    assert "LES-BT" not in ids and ids and d["money"] is False and all(x["amount"] == 0 for x in d["lessons"])
    assert all(x["mine"] for x in d["lessons"])
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "TCH-0001")
    d = _call(app, "GET", url)[1]
    bt = next(x for x in d["lessons"] if x["id"] == "LES-BT")
    assert d["all"] and d["money"] and bt["amount"] == 3000 and not bt["mine"]
    assert _call(app, "GET", f"/api/teacher/students/STU-9999/lessons?ym={YM}")[0] == 404



def test_transfer_needs_a_receipt_from_the_teacher(api, monkeypatch):
    """Перевод педагог зачитывает только с чеком: без receiptId — 400, чек уходит админам и даёт receiptId."""
    from aiohttp import FormData

    from tests.fakes import FakeBot
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "receipt_bank"})
    assert status == 400 and r["error"] == "receipt_required"
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",       # чужой/выдуманный чек
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "receipt_bank", "receiptId": "x"})
    assert status == 400 and r["error"] == "receipt_required"

    bot = FakeBot()

    async def upload():
        web_app = web.Application()
        register_teacher_api(web_app, dp, bot)
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            form = FormData()
            form.add_field("ym", YM)
            form.add_field("amount", "2000")
            form.add_field("file", b"\x89PNG", filename="r.png", content_type="image/png")
            h = {"Authorization": f"tma {make_init_data(user_id=TEACHER_TG)}"}
            resp = await client.post("/api/teacher/bills/student/STU-0001/receipt", headers=h, data=form)
            return resp.status, await resp.json()
        finally:
            await client.close()
    status, up = asyncio.run(upload())
    assert status == 200 and up["receiptId"] and up["notified"] >= 1
    assert bot.sent and "Чек перевода от педагога" in str(bot.sent[0])
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "receipt_bank", "receiptId": up["receiptId"]})
    assert status == 200 and r["credited"] == 2000


def test_teacher_cancels_only_own_payment_mark(api, monkeypatch):
    """«Убрать оплату» у педагога: свою отметку снимает, чужую (администратора) — 403."""
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "receipt_bank", "receiptId": "RC"})
    assert status == 200
    asyncio.run(dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 800, ADMIN_TG, ["TCH-0001"], None, "cash"))
    bill = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    rows = next(x for x in bill["rows"] if x["key"] == "TCH-0001")["paidRows"]
    mine = next(x for x in rows if x["mine"])
    admins = next(x for x in rows if not x["mine"])
    assert (mine["amount"], admins["amount"]) == (2000, 800)
    assert _call(app, "DELETE", f"/api/teacher/payments/{admins['id']}")[0] == 403
    status, r = _call(app, "DELETE", f"/api/teacher/payments/{mine['id']}")
    assert status == 200 and r["amount"] == 2000
    bill = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    row = next(x for x in bill["rows"] if x["key"] == "TCH-0001")
    assert [x["amount"] for x in row["paidRows"]] == [800]          # осталась только отметка администратора
    assert _call(app, "DELETE", "/api/teacher/payments/PAY-999999")[0] == 404


def test_billing_teacher_sets_subscription_amount_for_the_month(api, monkeypatch):
    """«Абонемент за этот месяц»: педагог своей группы ставит сумму на месяц; ниже оплаченного — 409; чужая группа — 404."""
    from tests.fakes import FakeBot
    app, dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000      # LES-3 — занятие группы в этом месяце
    bill = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    row = next(x for x in bill["rows"] if x["key"] == "SUB:GRP-0001")
    assert row["total"] == 6000 and row["ownGroup"] is True
    bot = FakeBot()
    status, r = _call(app, "PUT", "/api/teacher/bills/student/STU-0001/subscription", bot=bot,
                      json={"ym": YM, "groupId": "GRP-0001", "amount": 3000, "reason": "пришёл с 15 числа"})
    assert status == 200 and (r["old"], r["amount"]) == (6000, 3000)
    assert bot.sent and "изменил абонемент" in bot.sent[0][1] and "6000 ₽ → стало 3000 ₽" in bot.sent[0][1]
    bill = _call(app, "GET", f"/api/teacher/bills/student/STU-0001?ym={YM}")[1]
    assert next(x for x in bill["rows"] if x["key"] == "SUB:GRP-0001")["total"] == 3000
    asyncio.run(dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 3000, ADMIN_TG, ["SUB:GRP-0001"], None, "cash"))
    status, r = _call(app, "PUT", "/api/teacher/bills/student/STU-0001/subscription",
                      json={"ym": YM, "groupId": "GRP-0001", "amount": 2000})
    assert status == 409 and r["error"] == "paid_more"
    assert _call(app, "PUT", "/api/teacher/bills/student/STU-0001/subscription",
                 json={"ym": YM, "groupId": "GRP-0099", "amount": 1000})[0] == 404


def test_teacher_unpaid_screen_shows_own_groups_and_students(api, monkeypatch):
    """/unpaid — плитка «не оплатили за …» педагога: свои группы → ученики с долгом; без права счетов — 403."""
    app, dp = api
    assert _call(app, "GET", f"/api/teacher/unpaid?ym={YM}")[0] == 403
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    status, d = _call(app, "GET", f"/api/teacher/unpaid?ym={YM}")
    assert status == 200 and [g["id"] for g in d["groups"]] == ["GRP-0001"]
    g = d["groups"][0]
    assert [(s["name"], s["rest"], s["status"]) for s in g["students"]] == [
        ("Иванов Иван", 4800, "unpaid"), ("Петрова Анна", 800, "unpaid")]
    assert (g["rest"], g["unpaid"], d["rest"], d["unpaidStudents"]) == (5600, 2, 5600, 2)
    asyncio.run(dp["payment_service"].record_payment("STU-0002", "Петрова Анна", YM, 800, ADMIN_TG, None, None, "cash"))
    d = _call(app, "GET", f"/api/teacher/unpaid?ym={YM}")[1]
    assert d["rest"] == 4800 and next(s for s in d["groups"][0]["students"] if s["id"] == "STU-0002")["status"] == "paid"


def test_teacher_marks_student_left_only_without_debt(api, monkeypatch):
    """«Ушёл из группы» у педагога: долг не мешает — уход отмечается, а долг пишется в сообщение админам."""
    from tests.fakes import FakeBot
    app, dp = api
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000
    opts = _call(app, "GET", "/api/teacher/groups/GRP-0001")[1]["leaveOptions"]
    assert len(opts) == 2 and opts[0]["ym"] == YM
    bot = FakeBot()
    status, r = _call(app, "PUT", "/api/teacher/groups/GRP-0001/members/STU-0002/leave", bot=bot, json={"leftPeriod": opts[1]["ym"]})
    assert status == 200 and r["result"] == "marked" and r["debt"] == 6800          # абонемент 6000 + занятие 800
    assert bot.sent and "уходит из группы" in bot.sent[0][1] and "Петрова Анна" in bot.sent[0][1]
    assert "Долг ученика: 6800 ₽" in bot.sent[0][1]
    row = next(x for x in asyncio.run(dp["student_group_repo"].get_all()) if x.student_id == "STU-0002" and x.group_id == "GRP-0001")
    assert row.left_period == opts[1]["ym"]
    assert _call(app, "PUT", "/api/teacher/groups/GRP-0099/members/STU-0001/leave", json={"leftPeriod": YM})[0] == 404
    assert _call(app, "PUT", "/api/teacher/groups/GRP-0001/members/STU-0001/leave", json={"leftPeriod": "2020-01"})[0] == 400


def test_teacher_adds_student_to_own_group(api):
    """«Добавить ученика»: поиск по базе, новая карточка, существующий ученик; дубль имени — 409; чужая группа — 404."""
    from tests.fakes import FakeBot
    app, dp = api
    found = _call(app, "GET", "/api/teacher/students/search?q=иван")[1]["students"]
    assert [s["name"] for s in found] == ["Иванов Иван"] and _call(app, "GET", "/api/teacher/students/search?q=ив")[1]["students"] == []
    bot = FakeBot()
    status, r = _call(app, "POST", "/api/teacher/groups/GRP-0001/members", bot=bot, json={"name": "  Сидорова   Мария "})
    assert status == 200 and r["created"] and r["name"] == "Сидорова Мария"
    assert r["studentId"] in asyncio.run(dp["student_group_repo"].get_students_for_group("GRP-0001"))
    assert bot.sent and "добавил ученика" in bot.sent[0][1] and "новая карточка" in bot.sent[0][1]
    status, dup = _call(app, "POST", "/api/teacher/groups/GRP-0001/members", json={"name": "сидорова мария"})
    assert status == 409 and dup["error"] == "duplicate" and dup["students"][0]["id"] == r["studentId"]
    assert _call(app, "POST", "/api/teacher/groups/GRP-0001/members", json={"studentId": "STU-0001"})[0] == 409   # уже в группе
    assert _call(app, "POST", "/api/teacher/groups/GRP-0099/members", json={"name": "Новый Ученик"})[0] == 404
    assert _call(app, "POST", "/api/teacher/groups/GRP-0001/members", json={"name": "Я"})[0] == 400


def test_payment_notify_teacher_gets_notice_without_full_bill(api, monkeypatch):
    """PAYMENT_NOTIFY_TEACHER_IDS (Яковлева, Фомина): уведомление об оплате ученика своей группы приходит,
    хотя полного счёта у педагога нет."""
    from bot.services import payment_events
    app, dp = api
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "")
    monkeypatch.setattr(settings, "payment_notify_teacher_ids", "TCH-0001")
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))
    payment_events.setup(Bot(), dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"], dp["student_repo"],
                         dp["teacher_repo"])
    teacher_tg = next(u.tg_id for u in dp["user_repo"].items if u.teacher_id == "TCH-0001")

    async def pay_and_wait():
        await dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 2000, 555, None, payment_method="receipt_bank")
        await asyncio.gather(*payment_events._tasks)
    asyncio.run(pay_and_wait())
    assert [c for c, _ in sent] == [teacher_tg] and "2000 ₽" in sent[0][1] and "реквизитам" in sent[0][1]
    sent.clear()
    monkeypatch.setattr(settings, "payment_notify_teacher_ids", "")
    asyncio.run(pay_and_wait())
    assert sent == []                                               # без настройки — как раньше, тишина


def test_teacher_renames_student_of_own_group(api):
    """«✏️ Имя» в карточке ученика: только ученик своих групп; пусто/мусор — 400, точный дубль другого
    ученика — 409 до подтверждения; администраторам — «было / стало»."""
    app, dp = api
    dp["student_repo"].items.append(mk_student("STU-0009", "Чужой Ученик"))
    url = "/api/teacher/students/STU-0002"
    assert _call(app, "PATCH", "/api/teacher/students/STU-0009", json={"name": "Новое Имя"})[0] == 404   # не в его группах
    assert _call(app, "PATCH", url, json={"name": "  "})[0] == 400
    assert _call(app, "PATCH", url, json={"name": "123"})[0] == 400
    status, r = _call(app, "PATCH", url, json={"name": "Иванов  Иван"})
    assert status == 409 and r["error"] == "duplicate" and r["students"] == [{"id": "STU-0001", "name": "Иванов Иван"}]
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0002")).name == "Петрова Анна"
    bot = FakeBot()
    status, r = _call(app, "PATCH", url, json={"name": "  Петрова   Ания "}, bot=bot)
    assert status == 200 and r == {"ok": True, "name": "Петрова Ания"}
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0002")).name == "Петрова Ания"
    assert bot.sent[0][0] == ADMIN_TG and "Было: Петрова Анна" in bot.sent[0][1] and "Стало: Петрова Ания" in bot.sent[0][1]
    assert _call(app, "PATCH", url, json={"name": "Петрова Ания"})[1]["unchanged"] is True
    status, r = _call(app, "PATCH", url, json={"name": "Иванов Иван", "force": True})
    assert status == 200 and r["name"] == "Иванов Иван"


def _receipt_world(api, monkeypatch):
    from bot.repositories.pending_action_repo import KIND_RECEIPT
    from bot.services import payment_events
    from tests.test_admin_inbox_api import PendingRepoFake
    app, dp = api
    monkeypatch.setattr(settings, "full_bill_teacher_ids", "TCH-0001")
    payment_events.setup(None, dp["user_repo"], dp["teacher_group_repo"], dp["student_group_repo"], dp["student_repo"])
    dp["pending_repo"] = PendingRepoFake()
    action = asyncio.run(dp["pending_repo"].add(KIND_RECEIPT, "STU-0001", "Иванов Иван", YM, 9000, "receipt_bank",
                                                str(PARENT_TG)))
    return app, dp, action


def test_teacher_inbox_credits_no_more_than_rest(api, monkeypatch):
    """«Ждут решения» у педагога: чек на 9000 при остатке 4800 — зачесть можно только остаток, переплату — нет."""
    app, dp, action = _receipt_world(api, monkeypatch)
    url = f"/api/teacher/inbox/{action.action_id}/decide"
    status, r = _call(app, "POST", url, json={"approve": True, "force": True})
    assert status == 409 and r["allowOverpay"] is False and r["rest"] == 4800
    status, r = _call(app, "POST", url, json={"approve": True, "amount": 4800, "force": True})
    assert status == 200 and r["credited"] == 4800 and r["overpaid"] == 0


def test_teacher_telegram_buttons_have_no_overpay(api, monkeypatch):
    """Кнопки заявки в Telegram: у педагога нет «Всё равно зачесть (переплата)», и старая кнопка ничего не зачтёт;
    у администратора выбор остаётся."""
    from bot.handlers.client.my_bills import payment as pay_mod
    from bot.repositories.pending_action_repo import OPEN
    from tests.fakes import FakeCallbackQuery, FakeMessage
    app, dp, action = _receipt_world(api, monkeypatch)

    def press(user, data):
        msg = FakeMessage("Чек")
        msg.caption = None
        cb = FakeCallbackQuery(data, user_id=user.tg_id, message=msg)
        asyncio.run(pay_mod.cb_action_confirm(cb, user, dp["payment_service"], dp["student_repo"], dp["client_repo"],
                                              None, dp["pending_repo"]))
        text, kb = msg.screens[-1]
        return text, [b.text for row in kb.inline_keyboard for b in row]
    teacher = asyncio.run(dp["user_repo"].get_by_tg_id(TEACHER_TG))
    text, buttons = press(teacher, f"pact:{action.action_id}:9000:b:f")         # старая кнопка «переплата»
    assert "не больше остатка" in text and not any("переплата" in b for b in buttons)
    assert any("Зачесть остаток 4800" in b for b in buttons)
    assert dp["pending_repo"].items[0].status == OPEN
    assert not [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    admin = asyncio.run(dp["user_repo"].get_by_tg_id(ADMIN_TG))
    _, buttons = press(admin, f"pact:{action.action_id}:9000:b")
    assert any("переплата" in b for b in buttons)
