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
