"""Двойное «Сохранить» (случай 10.10.2026: две пары записаны дважды под одними номерами LES-002440/2441).

Три уровня защиты: номер занятия выдаётся под замком листа (LessonRepository.add_new), повтор того же
запроса за 30 с не создаёт занятия заново (record_create), кнопка в кабинете блокируется на время запроса."""
import asyncio
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_teacher_api
from bot.models import Lesson
from bot.models.enums import LessonType
from bot.repositories.base import BaseRepository
from bot.repositories.lesson_repo import LessonRepository
from tests.fakes import FakeSheetsClient, FakeWorksheet
from tests.test_admin_api import YM, make_api
from tests.test_telegram_auth import make_init_data

TEACHER_TG = 4242
HEADERS = ["lesson_id", "teacher_id", "teacher_name", "type", "student_1_id", "student_1_name", "student_2_id",
           "student_2_name", "date", "duration_min", "earned", "recorded_at", "updated_at", "attendees", "group_id",
           "student_3_id", "student_3_name", "student_4_id", "student_4_name"]


class _SlowSheet(FakeWorksheet):
    """Запись в Google идёт сотни миллисекунд — пока одна корутина пишет, другая успевает прочитать номера."""
    def append_row(self, values, value_input_option=None):
        time.sleep(0.05)
        super().append_row(values, value_input_option)


def _lesson():
    return Lesson(lesson_id="", teacher_id="TCH-0001", teacher_name="Река", type=LessonType.INDIVIDUAL,
                  student_1_id="STU-1", student_1_name="А", student_2_id="STU-2", student_2_name="Б",
                  date="2026-10-09", duration_min=60, earned=0, recorded_at="", updated_at="")


def test_concurrent_add_new_gets_distinct_numbers():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    ws = _SlowSheet(HEADERS, [["LES-002439", "TCH-0001", "Река", "individual", "STU-1", "А", "", "", "2026-10-08", 45, 0,
                                "", "", "", "", "", "", "", ""]])
    repo = LessonRepository(FakeSheetsClient(ws), "lessons")

    async def both():
        await repo.get_all()                                   # кеш прогрет, как на проде
        return await asyncio.gather(repo.add_new(_lesson()), repo.add_new(_lesson()))
    a, b = asyncio.run(both())
    assert {a.lesson_id, b.lesson_id} == {"LES-002440", "LES-002441"}
    assert sorted(r[0] for r in ws.rows) == ["LES-002439", "LES-002440", "LES-002441"]
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()


def test_same_record_request_twice_creates_lessons_once(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    before = len(dp["lesson_repo"].items)
    body = {"kind": "pair", "date": f"{YM}-05", "durationMin": 60, "studentIds": ["STU-0001"]}
    dp["student_repo"].items[0].partner_id, dp["student_repo"].items[1].partner_id = "STU-0002", "STU-0001"

    async def run():
        app = web.Application()
        register_teacher_api(app, dp, None)
        client = TestClient(TestServer(app))
        await client.start_server()
        h = {"Authorization": f"tma {make_init_data(user_id=TEACHER_TG)}"}
        try:
            async def post(b):
                r = await client.post("/api/teacher/record", headers=h, json=b)
                return r.status, await r.json()
            first, second = await asyncio.gather(post(body), post(body))       # двойное касание
            third = await post({**body, "durationMin": 45})                      # другое занятие — пишется
            return first, second, third
        finally:
            await client.close()
    first, second, third = asyncio.run(run())
    assert first[0] == second[0] == 200 and third[0] == 200
    assert sorted([bool(first[1].get("repeat")), bool(second[1].get("repeat"))]) == [False, True]
    assert first[1]["lessons"] == second[1]["lessons"]
    new = dp["lesson_repo"].items[before:]
    assert [(x.duration_min, x.student_1_id, x.student_2_id) for x in new] == [(60, "STU-0001", "STU-0002"),
                                                                               (45, "STU-0001", "STU-0002")]
    assert len({x.lesson_id for x in new}) == 2


def _api_with_teacher(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    return dp


def _post_all(dp, bodies, concurrent=False):
    async def run():
        app = web.Application()
        register_teacher_api(app, dp, None)
        client = TestClient(TestServer(app))
        await client.start_server()
        h = {"Authorization": f"tma {make_init_data(user_id=TEACHER_TG)}"}
        try:
            async def post(b):
                r = await client.post("/api/teacher/record", headers=h, json=b)
                return r.status, await r.json()
            if concurrent:
                return list(await asyncio.gather(*(post(b) for b in bodies)))
            return [await post(b) for b in bodies]
        finally:
            await client.close()
    return asyncio.run(run())


def test_second_solo_same_day_needs_confirmation(monkeypatch):
    """Три урока с одной ученицей в один день (случай Хуснутдинова и Бущук 10.10.2026): второй и третий —
    после подтверждения; каждое нажатие — свой requestId, поэтому повтором не считается."""
    dp = _api_with_teacher(monkeypatch)
    before = len(dp["lesson_repo"].items)
    solo = {"kind": "soloist", "date": f"{YM}-06", "durationMin": 45, "studentIds": ["STU-0002"]}
    r1, r2, r3, r4 = _post_all(dp, [{**solo, "requestId": "press-0001"}, {**solo, "requestId": "press-0002"},
                                    {**solo, "requestId": "press-0003", "confirmSameDay": True},
                                    {**solo, "requestId": "press-0004", "confirmSameDay": True}])
    assert r1[0] == 200 and r1[1]["created"] == 1
    assert r2[0] == 409 and r2[1]["error"] == "same_day" and "Петрова Анна" in r2[1]["message"]
    assert r3[0] == 200 and r4[0] == 200 and not r3[1].get("repeat") and not r4[1].get("repeat")
    new = dp["lesson_repo"].items[before:]
    assert [(x.date, x.student_1_id) for x in new] == [(f"{YM}-06", "STU-0002")] * 3 and len({x.lesson_id for x in new}) == 3


def test_same_press_sent_twice_is_saved_once(monkeypatch):
    """Сетевой повтор того же нажатия (тот же requestId) — один результат, даже для подтверждённого соло."""
    dp = _api_with_teacher(monkeypatch)
    before = len(dp["lesson_repo"].items)
    body = {"kind": "soloist", "date": f"{YM}-07", "durationMin": 45, "studentIds": ["STU-0002"], "requestId": "press-0100"}
    first, second = _post_all(dp, [body, body], concurrent=True)
    assert first[0] == second[0] == 200 and sorted([bool(first[1].get("repeat")), bool(second[1].get("repeat"))]) == [False, True]
    assert len(dp["lesson_repo"].items) == before + 1


def test_soloist_batch_is_not_saved_partially(monkeypatch):
    """Несколько солистов разом: если у одного уже есть соло в этот день — ничего не записывается до подтверждения."""
    dp = _api_with_teacher(monkeypatch)
    _post_all(dp, [{"kind": "soloist", "date": f"{YM}-08", "durationMin": 45, "studentIds": ["STU-0002"], "requestId": "press-0200"}])
    before = len(dp["lesson_repo"].items)
    batch = {"kind": "soloist", "date": f"{YM}-08", "durationMin": 45, "studentIds": ["STU-0001", "STU-0002"]}
    (status, r), = _post_all(dp, [{**batch, "requestId": "press-0201"}])
    assert status == 409 and r["error"] == "same_day" and "Петрова Анна" in r["message"] and "Иванов" not in r["message"]
    assert len(dp["lesson_repo"].items) == before
    (status, r), = _post_all(dp, [{**batch, "requestId": "press-0202", "confirmSameDay": True}])
    assert status == 200 and r["created"] == 2
