"""API кабинета педагога (/api/teacher/*): роль, свои занятия, группы, статистика, сдача периода."""
import asyncio
from datetime import date

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_teacher_api
from bot.models.enums import LessonType, PaymentStatus
from bot.services.diary_service import DiaryService
from config.settings import settings
from tests.fakes import (
    AthleteTaskRepoFake, FakeBot, TrainingEntryRepoFake,
    mk_entry, mk_lesson, mk_payment, mk_submission, mk_user,
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
    assert h["prevSubmitted"] is True and h["periodSubmitted"] is False        # август сдан, текущий — нет

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
                      json={"kind": "soloist", "date": f"{YM}-14", "durationMin": 45, "studentIds": ["STU-0002"]})
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
                      json={"ym": YM, "key": "TCH-0001", "amount": 2000, "method": "cash"})
    assert status == 200 and r["credited"] == 2000
    assert [p.total_amount for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID] == [2000]

    status, m2 = _call(app, "GET", f"/api/teacher/bills/student/STU-0001/marks?ym={YM}&key=TCH-0001")
    assert m2["ledger"]["paid"] == 2000 and [x["paid"] for x in m2["marks"]] == [True, False, False]
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0001", "amount": 0})[0] == 400
    assert _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay",
                 json={"ym": YM, "key": "TCH-0001", "amount": 100, "method": "yookassa"})[0] == 400


def test_billing_teacher_cannot_overpay_silently(api, monkeypatch):
    app, _dp = api
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0001")
    body = {"ym": YM, "key": "TCH-0001", "amount": 9000, "method": "cash"}
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json=body)
    assert status == 409 and r["needsConfirm"] and r["rest"] == 4800
    status, r = _call(app, "POST", "/api/teacher/bills/student/STU-0001/pay", json={**body, "force": True})
    assert status == 200 and r["credited"] == 9000 and r["overpaid"] == 4200


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
                 json={"ym": YM, "key": "TCH-0002", "amount": 3000, "method": "cash"})[0] == 404
