import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from aiogram.types import Chat, Message, Update, User

from bot.middlewares.private_only import PrivateChatsOnlyMiddleware
from bot.models import Student
from bot.services.schedule_pilot import SchedulePilot, StudentNames, parse_schedule
from tests.fakes import mk_user
from tests.test_admin_api import ADMIN_TG, PARENT_TG, _call, make_api
from tests.test_teacher_api import _call as call_teacher

CHAT = -1001111111111
PUBLISHED = datetime(2026, 11, 18, 21, 5, tzinfo=timezone.utc)  # четверг в Москве
SCHEDULE = """Добрый вечер

Расписание со мной на четверг

16:30 Егор Катя 60
17:30 Мирон София
18:15 Ким Алина
19:00 Коля Соня
19:45
20:30 Антон Полина"""
TUESDAY = """Вторник:
♦️ 16.15 - Ира М.
♦️ 17.00 Коля и Соня
♦️ 18.00 Егор и Катя
♦️ 19.00 - 20.30 группа
♦️ 20.30 Антон и Полина"""


def _message(text=SCHEDULE, *, uid=ADMIN_TG, chat=CHAT, edited=False):
    return Message(message_id=17, date=PUBLISHED, chat=Chat(id=chat, type="supergroup"),
                   from_user=User(id=uid, is_bot=False, first_name="Педагог"), text=text,
                   edit_date=int((PUBLISHED + timedelta(minutes=5)).timestamp()) if edited else None)


def test_sample_schedule_and_unknown_duration():
    parsed = parse_schedule(SCHEDULE, date(2026, 11, 19))
    assert parsed is not None
    day, slots = parsed
    assert day == date(2026, 11, 19)
    assert [(s.time, s.duration, s.suggested) for s in slots] == [
        ("16:30", 60, False), ("17:30", 45, True), ("18:15", 45, True),
        ("19:00", 45, True), ("19:45", None, False), ("20:30", None, False),
    ]
    assert slots[4].names == ""
    for text in ("Расписание Влада на четверг", "Расписание Кирилла на четверг", "Расписание на четверг",
                 "Расписание со мной и Владом на четверг"):
        assert parse_schedule(f"{text}\n16:30 Егор Катя", day) is None
    assert parse_schedule(f"{SCHEDULE}\nВлад\n21:15 Егор Катя", day) is None


@pytest.mark.parametrize(
    ("text", "day", "expected"),
    [
        ("Понедельник:\n♦️ 19.45 Антон и Полина", 23, [("19:45", "Антон и Полина", None, None)]),
        (TUESDAY, 24, [
            ("16:15", "Ира М.", 45, None), ("17:00", "Коля и Соня", None, None), ("18:00", "Егор и Катя", None, None),
            ("19:00", "группа", 90, "20:30"), ("20:30", "Антон и Полина", None, None),
        ]),
        ("Четверг:\n♦️ 18.00 Егор и Катя 60\n♦️ 19.00 свободно", 19,
         [("18:00", "Егор и Катя", 60, None), ("19:00", "", None, None)]),
        ("Пятница\n♦️ 16:45 Коля Соня 60\n♦️ 17:45 Егор и Катя 45", 20,
         [("16:45", "Коля Соня", 60, None), ("17:45", "Егор и Катя", 45, None)]),
    ],
)
def test_weekday_posts_in_actual_format(text, day, expected):
    parsed = parse_schedule(text, date(2026, 11, 19))
    assert parsed is not None
    when, slots = parsed
    assert when == date(2026, 11, day)
    assert [(s.time, s.names, s.duration, s.end) for s in slots] == expected


def test_weekday_post_must_describe_only_the_author_and_one_day():
    published = date(2026, 11, 19)
    for unrelated in (
        f"Влад:\n{TUESDAY}",
        TUESDAY.replace("Вторник:", "Вторник Кирилл:"),
        TUESDAY.replace("Вторник:", "Вторник:\nВласов"),
        f"{TUESDAY}\nЧетверг:\n♦️ 19.00 свободно",
        TUESDAY.replace("19.00 - 20.30", "19.00 - 18.30"),
    ):
        assert parse_schedule(unrelated, published) is None


def test_matching_requires_a_unique_student_or_pair():
    students = StudentNames([
        Student("a", "Зотов Антон", partner_id="p"),
        Student("p", "Рычкова Полина", partner_id="a"),
        Student("b", "Иванов Антон"),
        Student("c", "Ким Алина"),
        Student("d", "Ким Алексей", partner_id="e"),
        Student("e", "Иванова Алина", partner_id="d"),
        Student("m", "Макарова Ирина"),
        Student("n", "Николаева Ирина"),
        Student("s", "Сидоров Саша", partner_id="em"),
        Student("em", "Орлова Эмилия", partner_id="s"),
        Student("e2", "Иванова Эмилия"),
    ])
    assert students.match("Антон Полина") == ("a", "p")
    assert students.match("Антон и Полина") == ("a", "p")
    assert students.match("Ира М.") == ("m",)
    assert students.match("Ира") == ()
    assert students.match("Саша и Эмили") == ("em", "s")
    assert students.match("Ким Алина") == ()
    assert students.match("Антон") == ()


def test_only_pilot_posts_are_stored_and_edits_replace_them(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="bot.services.schedule_pilot")

    async def run():
        db = tmp_path / "drafts.db"
        pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", db)
        middleware = PrivateChatsOnlyMiddleware(pilot)

        async def private_handler(*args):
            raise AssertionError("Групповое сообщение дошло до обычного обработчика")

        await middleware(private_handler, Update(update_id=1, message=_message(uid=22)), {})
        await middleware(private_handler, Update(update_id=2, message=_message(chat=-100777)), {})
        kirill = SCHEDULE.replace("со мной", "Кирилла")
        await middleware(private_handler, Update(update_id=3, message=_message(text=kirill)), {})
        assert not db.exists()

        original = Update(update_id=4, message=_message())
        await middleware(private_handler, original, {})
        await middleware(private_handler, original, {})
        assert len([record for record in caplog.records if "Черновик" in record.message]) == 1
        edited = SCHEDULE.replace("20:30 Антон Полина", "20:30 Антон Полина 45")
        await middleware(private_handler, Update(update_id=5, edited_message=_message(text=edited, edited=True)), {})
        await middleware(private_handler, original, {})
        restarted = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", db)
        drafts = await restarted.drafts([])
        assert len(drafts) == 1
        assert drafts[0]["date"] == "2026-11-19"
        assert drafts[0]["slots"][-1]["duration"] == 45

        await middleware(private_handler, Update(update_id=6, edited_message=_message(text=kirill, edited=True)), {})
        await middleware(private_handler, original, {})
        assert await restarted.drafts([]) == []

    asyncio.run(run())


def test_drafts_visible_only_in_owner_cabinet(monkeypatch, tmp_path):
    app, dp = make_api(monkeypatch)
    lessons_before = list(dp["lesson_repo"].items)
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", tmp_path / "drafts.db")
    dp["schedule_pilot"] = pilot
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    asyncio.run(pilot.receive(_message(TUESDAY + "\n♦️ 21:15 свободно"), 1, edited=False))
    dp["user_repo"].items.append(mk_user(tg_id=123, is_admin=True, teacher_id="TCH-0001"))
    route = "/api/teacher/schedule-drafts"
    assert "schedulePilot" not in _call(app, "GET", "/api/admin/home")[1]
    assert call_teacher(app, "GET", "/api/teacher/home", tg_id=ADMIN_TG)[1]["schedulePilot"] is True
    assert "schedulePilot" not in call_teacher(app, "GET", "/api/teacher/home", tg_id=123)[1]
    assert call_teacher(app, "GET", route, headers={})[0] == 401
    assert call_teacher(app, "GET", route, tg_id=PARENT_TG)[0] == 403
    assert call_teacher(app, "GET", route, tg_id=123)[0] == 404
    status, data = call_teacher(app, "GET", route, tg_id=ADMIN_TG)
    assert status == 200
    slots = data["drafts"][0]["slots"]
    assert slots[0]["text"] == "Ира М."
    assert (slots[3]["kind"], slots[3]["time"], slots[3]["end"], slots[3]["duration"]) == ("group", "19:00", "20:30", 90)
    assert slots[-1]["kind"] == "free"
    assert dp["lesson_repo"].items == lessons_before
