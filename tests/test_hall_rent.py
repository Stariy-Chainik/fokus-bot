"""Аренда зала помесячно (решение владельца 27.10.2026): педагог с прямой оплатой (Клецова) перечисляет школе
аренду за индивидуальные одной суммой в конце месяца, школа платит ему зарплату за группы — две цифры рядом.
Ставка у отдельных учеников своя (HALL_RENT_STUDENT_RATES); с месяца HALL_RENT_SETTLE_SINCE_PERIOD —
статус «получена», кнопка у администратора, напоминание педагогу за прошлый месяц."""
import asyncio

from bot.services import hall_rent
from config.settings import settings
from tests.fakes import mk_lesson, mk_student
from tests.test_admin_api import ADMIN_TG, YM, _call as admin_call, api as admin_api  # noqa: F401
from tests.test_teacher_api import _call as teacher_call, api as teacher_api  # noqa: F401


def _direct(monkeypatch, since=YM):
    """TCH-0001 — прямая оплата: аренда 500 за час, с Петровой (STU-0002) — 800; учёт с месяца `since`."""
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    monkeypatch.setattr(settings, "hall_rent_per_lesson", "TCH-0001:500")
    monkeypatch.setattr(settings, "hall_rent_student_rates", "TCH-0001:STU-0002:800")
    monkeypatch.setattr(settings, "hall_rent_settle_since_period", since)


def _add_petrova_lesson(dp, day=20, duration=90):
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-P", teacher, f"{YM}-{day:02d}", duration=duration,
                                             students=[("STU-0002", "Петрова Анна")]))


def test_rent_per_student_and_status(admin_api, monkeypatch):  # noqa: F811
    app, dp = admin_api
    _direct(monkeypatch)
    _add_petrova_lesson(dp)                                    # 90 мин с Петровой — 800 × 2 часа
    lessons = asyncio.run(dp["lesson_repo"].get_by_teacher_and_period("TCH-0001", YM))
    assert hall_rent.month_rent(lessons) == 500 + 500 + 1600   # два урока Иванова по 500 + Петрова 1600
    by = hall_rent.rent_by_student(lessons)
    assert (by["STU-0001"].lessons, by["STU-0001"].amount, by["STU-0002"].amount) == (2, 1000, 1600)
    st = asyncio.run(hall_rent.status(dp["hall_rent_repo"], "TCH-0001", YM, lessons))
    assert (st["accrued"], st["paid"], st["rest"], st["status"], st["settle"], st["lessons"]) == (2600, 0, 2600, "due", True, 3)
    assert hall_rent.settled("2026-01") is False                # до месяца начала учёта — только справочно


def test_admin_marks_rent_received_and_teacher_sees_it(admin_api, monkeypatch):  # noqa: F811
    app, dp = admin_api
    _direct(monkeypatch)
    row = next(t for t in admin_call(app, "GET", f"/api/admin/salaries?ym={YM}")[1]["teachers"] if t["id"] == "TCH-0001")
    assert row["rent"]["accrued"] == 1000 and row["rent"]["status"] == "due" and row["rent"]["settle"] is True
    card = admin_call(app, "GET", f"/api/admin/salaries/TCH-0001?ym={YM}")[1]
    assert card["rent"]["students"] == [{"id": "STU-0001", "name": "Иванов Иван", "lessons": 2, "amount": 1000}]
    assert card["total"] == 1333                                # зарплата за группу — отдельно, без взаимозачёта
    # частями: 400, потом остаток 600
    st, r = admin_call(app, "POST", "/api/admin/salaries/TCH-0001/rent", json={"periodMonth": YM, "amount": 400, "comment": "перевод"})
    assert st == 200 and r["rent"]["status"] == "partial" and r["rent"]["rest"] == 600
    st, r = admin_call(app, "POST", "/api/admin/salaries/TCH-0001/rent", json={"periodMonth": YM, "amount": 600})
    assert st == 200 and r["rent"]["status"] == "paid"
    s = admin_call(app, "GET", f"/api/admin/salaries?ym={YM}")[1]
    assert s["rent"] == {"accrued": 1000, "paid": 1000, "settle": True}
    # снять отметку — снова частично
    assert admin_call(app, "DELETE", f"/api/admin/rent/{r['id']}")[0] == 200
    assert admin_call(app, "GET", f"/api/admin/salaries/TCH-0001?ym={YM}")[1]["rent"]["paid"] == 400
    # валидация: сумма ≤ 0, чужой педагог без прямой оплаты
    assert admin_call(app, "POST", "/api/admin/salaries/TCH-0001/rent", json={"periodMonth": YM, "amount": 0})[0] == 400
    assert admin_call(app, "POST", "/api/admin/salaries/TCH-0099/rent", json={"periodMonth": YM, "amount": 100})[0] == 404
    assert admin_call(app, "DELETE", "/api/admin/rent/HR-999999")[0] == 404


def test_teacher_cabinet_shows_rent_and_reminder(teacher_api, monkeypatch):  # noqa: F811
    app, dp = teacher_api
    _direct(monkeypatch, since="2026-01")
    st = teacher_call(app, "GET", f"/api/teacher/stats?ym={YM}")[1]
    assert st["rent"]["accrued"] == 1000 and st["rent"]["status"] == "due"
    assert [(x["id"], x["rent"]) for x in st["direct"]["students"]] == [("STU-0001", 1000)]
    h = teacher_call(app, "GET", "/api/teacher/home")[1]
    assert h["rentMonth"] == 1000 and h["rentDue"] is None        # за прошлый месяц занятий нет — напоминания нет
    # прошлый месяц: урок с Петровой на 800, аренда не получена → напоминание на сводке
    prev = h["prevPeriod"]
    teacher = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items.append(mk_lesson("LES-PREV", teacher, f"{prev}-10", students=[("STU-0002", "Петрова Анна")]))
    h = teacher_call(app, "GET", "/api/teacher/home")[1]
    assert h["rentDue"] == {"period": prev, "rest": 800}
    asyncio.run(dp["hall_rent_repo"].add("TCH-0001", prev, 800, ADMIN_TG))
    assert teacher_call(app, "GET", "/api/teacher/home")[1]["rentDue"] is None
    # обычный педагог без прямой оплаты — аренды нет
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "")
    assert teacher_call(app, "GET", f"/api/teacher/stats?ym={YM}")[1]["rent"] is None


def test_notification_reaches_teacher(admin_api, monkeypatch):  # noqa: F811
    app, dp = admin_api
    _direct(monkeypatch)
    dp["student_repo"].items.append(mk_student("STU-0009", "Кто-то"))
    sent = []

    class Bot:
        async def send_message(self, tg_id, text, **kw):
            sent.append((tg_id, text))
    asyncio.run(dp["user_repo"].add(555, teacher_id="TCH-0001"))
    ok = asyncio.run(hall_rent.notify_teacher(Bot(), dp["user_repo"], "TCH-0001", hall_rent.received_text(YM, 400, 600)))
    assert ok and sent[0][0] == 555 and "400 ₽" in sent[0][1] and "Остаток за месяц: 600 ₽" in sent[0][1]
    assert asyncio.run(hall_rent.notify_teacher(Bot(), dp["user_repo"], "TCH-0077", "x")) is False
