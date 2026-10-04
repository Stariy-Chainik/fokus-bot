"""Лента изменений администратора: сервисы и репозитории пишут события, API отдаёт их с именами."""
import asyncio
from datetime import date

import pytest

from bot.models.enums import LessonType
from bot.services import activity
from tests.fakes import ActivityRepoFake
from tests.test_admin_api import YM, _call, make_api

TEACHER_TG = 4242


@pytest.fixture()
def feed(monkeypatch):
    repo = ActivityRepoFake()
    activity.setup(repo)
    yield repo
    activity.setup(None)


def test_lessons_and_payments_are_recorded(feed, monkeypatch):
    app, dp = make_api(monkeypatch)
    dp["activity_repo"] = feed
    teacher = dp["teacher_repo"].items[0]
    lesson = asyncio.run(dp["lesson_service"].create(
        teacher, LessonType.INDIVIDUAL, date.today().isoformat(), 45,
        student_1_id="STU-0002", student_1_name="Петрова Анна", bypass_period_lock=True))
    asyncio.run(dp["lesson_service"].delete(lesson.lesson_id, bypass_period_lock=True))
    asyncio.run(dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 2000, 826576855, ["TCH-0001"],
                                                     "отмечено вручную", "cash"))
    texts = [e.text for e in feed.items]
    assert texts[0].startswith("Отмечено занятие: TCH-0001") and "STU-0002" in texts[0] and "за педагога" in texts[0]
    assert texts[1].startswith("Удалено занятие: TCH-0001") and "администратором" in texts[1]
    assert texts[2] == "Оплата 2000 ₽: STU-0001 · " + YM + " · cash · отмечено вручную"
    assert [e.kind for e in feed.items] == ["lesson", "lesson", "payment"]


def test_feed_api_resolves_names_and_actors(feed, monkeypatch):
    app, dp = make_api(monkeypatch)
    dp["activity_repo"] = feed
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    asyncio.run(feed.add("payment", "Оплата 2000 ₽: STU-0001 · " + YM + " · cash", actor=TEACHER_TG, ref="STU-0001"))
    asyncio.run(feed.add("payment", "Оплата 4400 ₽: STU-0002 · " + YM + " · yookassa_sbp", actor=0))
    asyncio.run(feed.add("student", "Ученик STU-0002 добавлен в группу GRP-0001 с " + YM, actor=826576855))
    status, d = _call(app, "GET", "/api/admin/activity?days=1")
    assert status == 200 and len(d["events"]) == 3
    by_text = {e["text"]: e["who"] for e in d["events"]}
    assert by_text["Оплата 2000 ₽: Иванов Иван · " + YM + " · cash"] == "Река Станислав"   # педагог по имени
    assert by_text["Оплата 4400 ₽: Петрова Анна · " + YM + " · yookassa_sbp"] == "ЮКасса"
    assert by_text["Ученик Петрова Анна добавлен в группу БП Джаз с " + YM].startswith("админ")
    assert _call(app, "GET", "/api/admin/activity?days=1&kind=payment")[1]["events"].__len__() == 2
    assert _call(app, "GET", "/api/admin/home")[1]["activityToday"] == 3


def test_feed_is_optional_and_never_breaks_writes(monkeypatch):
    """Без листа ленты (тесты, скрипты) запись — no-op; ошибка листа не роняет сценарий."""
    activity.setup(None)
    asyncio.run(activity.record("lesson", "x"))

    class Broken:
        async def add(self, *a, **k):
            raise RuntimeError("sheet down")
    activity.setup(Broken())
    try:
        asyncio.run(activity.record("lesson", "x"))
    finally:
        activity.setup(None)


def test_feed_events_link_to_screens_and_carry_receipt(feed, monkeypatch):
    """Тап по событию ведёт на экран (оплата — счёт ученика за месяц), чек педагога виден в ленте
    (решение владельца 04.10.2026)."""
    app, dp = make_api(monkeypatch)
    dp["activity_repo"] = feed
    asyncio.run(dp["payment_service"].record_payment("STU-0001", "Иванов Иван", YM, 2000, 826576855, ["TCH-0001"],
                                                     "отметил педагог · чек", "receipt_bank", receipt_file_id="AgACfile1"))
    asyncio.run(activity.record(activity.GROUP, "Группа GRP-0001 переименована", actor=826576855, ref="GRP-0001"))
    status, d = _call(app, "GET", "/api/admin/activity?days=1")
    assert status == 200
    pay = next(e for e in d["events"] if e["kind"] == "payment")
    group = next(e for e in d["events"] if e["kind"] == "group")
    assert (pay["studentId"], pay["ym"], pay["hasFile"]) == ("STU-0001", YM, True)
    assert pay["ref"] == "STU-0001 file:AgACfile1" and "AgACfile1" not in pay["text"]
    assert (group["groupId"], group["hasFile"]) == ("GRP-0001", False) and "studentId" not in group
    # файл: чужой ts/ref — 404; настоящий без бота — 503 (бот отдаёт его через getFile)
    assert _call(app, "GET", "/api/admin/activity/file?ts=x&ref=STU-0001%20file:AgACfile1")[0] == 404
    assert _call(app, "GET", f"/api/admin/activity/file?ts={pay['ts'].replace(' ', '%20')}&ref=STU-0001%20file:AgACfile1")[0] == 503
