import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import pytest
from aiogram.types import Chat, Message, Update, User

from bot.middlewares.private_only import PrivateChatsOnlyMiddleware
from bot.models import Student
from bot.services import schedule_pilot
from bot.services.billing_service import build_billing_rows
from bot.services.schedule_pilot import SchedulePilot, SchedulePilots, StudentNames, parse_schedule
from tests.fakes import mk_lesson, mk_teacher, mk_user
from tests.test_admin_api import ADMIN_TG, PARENT_TG, _call, await_teacher, make_api
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
            ("16:15", "Ира М.", 45, None), ("17:00", "Коля и Соня", 60, None), ("18:00", "Егор и Катя", 60, None),
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


def test_plain_schedule_title_defaults_to_publication_day_and_can_specify_a_day():
    published = date(2026, 11, 19)
    for title, expected in (
        ("Расписание:", published),
        ("Добрый день!\nРасписание на пятницу", date(2026, 11, 20)),
        ("Добрый вечер!\nРасписание занятий на пятницу", date(2026, 11, 20)),
        ("Моё расписание\nПятница:", date(2026, 11, 20)),
    ):
        text = f"{title}\n17.00 Иванов Иван\n18:00 Петрова Анна"
        parsed = parse_schedule(text, published, accept_weekday=False, accept_plain_title=True)
        assert parsed is not None
        day, slots = parsed
        assert day == expected
        assert [(slot.time, slot.names, slot.duration) for slot in slots] == [
            ("17:00", "Иванов Иван", 60), ("18:00", "Петрова Анна", None),
        ]

    for text in (
        "Расписание Леры на четверг\n17:00 Иванов Иван",
        "Расписание занятий Леры на четверг\n17:00 Иванов Иван",
        "Расписание\nЛера\n17:00 Иванов Иван",
        "Обсудим расписание завтра\n17:00 Иванов Иван",
        "Расписание\n17:00\n18:00 свободно",
    ):
        assert parse_schedule(text, published, accept_weekday=False, accept_plain_title=True) is None


def test_duration_inference_uses_next_row_and_keeps_explicit_values():
    text = """Четверг:
15:00 Иван
16:30 Анна
17:30 свободно
18:15 Мирон 30
19:15 группа 45
20:00 София
21:15 Антон
"""
    parsed = parse_schedule(text, date(2026, 11, 19))
    assert parsed is not None
    assert [(s.duration, s.suggested) for s in parsed[1]] == [
        (90, True), (60, True), (None, False), (30, False), (45, False), (None, False), (None, False),
    ]


@pytest.mark.parametrize(
    ("text", "day", "times"),
    [
        ("С.А.\n17:00 Иванов Иван", 19, ["17:00"]),
        ("С.А.:\nСуббота\n10.30 Иванов Иван\n15.15 Анна", 21, ["10:30", "15:15"]),
        ("С.А. на субботу\n17:00 Иванов Иван", 21, ["17:00"]),
        ("С.А. 17:00 Иванов Иван", 19, ["17:00"]),
        ("Пятница:\nС.А.\nРасписание со мной пт\n♦️ 17:00 Иванов Иван\n♦️\n♦️ 19:15 Анна", 20,
         ["17:00", "19:15"]),
        ("С.А. Понедельник\n17:00 Иванов Иван", 23, ["17:00"]),
        ("С.А. Пн\n17:00 Иванов Иван", 23, ["17:00"]),
        ("С.А. - пн.\n17:00 Иванов Иван\n18:00 -", 23, ["17:00", "18:00"]),
        ("Понедельник, С.А.\n17:00 Иванов Иван", 23, ["17:00"]),
        ("Пн С.А.\n17:00 Иванов Иван", 23, ["17:00"]),
        ("Понедельник (С.А.)\n17:00 Иванов Иван", 23, ["17:00"]),
        ("Расписание С.А. на пн\n17:00 Иванов Иван", 23, ["17:00"]),
        ("С.А. в понедельник 23.11\n17:00 Иванов Иван", 23, ["17:00"]),
        ("C.A. пн 23.11\n17:00 Иванов Иван", 23, ["17:00"]),
        ("С.А\nПн.\n17:00 Иванов Иван", 23, ["17:00"]),
        ("📋 С.А. (пн)\n17:00 Иванов Иван", 23, ["17:00"]),
    ],
)
def test_owner_initials_mark_personal_schedule(text, day, times):
    published = date(2026, 11, 19)
    assert parse_schedule(text, published, accept_plain_title=True) is None
    parsed = parse_schedule(text, published, accept_owner_initials=True)
    assert parsed is not None
    when, slots = parsed
    assert when == date(2026, 11, day)
    assert [slot.time for slot in slots] == times
    assert slots[0].names == "Иванов Иван"


def test_owner_initials_do_not_override_author_or_conflicting_date():
    published = date(2026, 11, 19)
    for text in (
        "10.30 Иванов Иван\n15.15 Анна",
        "С.А.\nВлад\n17:00 Иванов Иван",
        "С.А. Влад\n17:00 Иванов Иван",
        "Расписание Владa на субботу\nС.А.\n17:00 Иванов Иван",
        "С.А. на субботу\nПятница\n17:00 Иванов Иван",
        "С.А. вт 23.11\n17:00 Иванов Иван",
    ):
        assert parse_schedule(text, published, accept_owner_initials=True) is None


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
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: date(2026, 11, 24))
    app, dp = make_api(monkeypatch)
    lessons_before = list(dp["lesson_repo"].items)
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", tmp_path / "drafts.db")
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    asyncio.run(pilot.receive(_message(TUESDAY + "\n♦️ 21:15 свободно"), 1, edited=False))
    dp["user_repo"].items.append(mk_user(tg_id=123, is_admin=True, teacher_id="TCH-0001"))
    route = "/api/teacher/schedule-drafts"
    assert "schedulePilot" not in _call(app, "GET", "/api/admin/home")[1]
    home = call_teacher(app, "GET", "/api/teacher/home", tg_id=ADMIN_TG)[1]
    assert home["schedulePilot"] is True
    assert home["scheduleTodayPending"] == 5 and home["scheduleCompletedToday"] is False
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


def test_linked_teachers_see_only_their_posts_and_resolve_visible_students(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: date(2026, 11, 19))
    app, dp = make_api(monkeypatch)
    database = tmp_path / "teacher-sources.db"
    nazar, ivan = 222, 333
    dp["user_repo"].items.extend([mk_user(tg_id=nazar, teacher_id="TCH-0007"), mk_user(tg_id=ivan, teacher_id="TCH-0003")])
    dp["teacher_repo"].items.extend([mk_teacher("TCH-0007", "Назар"), mk_teacher("TCH-0003", "Иван")])
    dp["teacher_group_repo"]._t2g["TCH-0003"] = ["GRP-0001"]
    pilots = SchedulePilots([
        SchedulePilot(CHAT, nazar, "TCH-0007", database, accept_weekday=False, accept_plain_title=True),
        SchedulePilot(CHAT, ivan, "TCH-0003", database, accept_weekday=False, accept_plain_title=True),
    ])
    dp["schedule_pilots"] = pilots
    route = "/api/teacher/schedule-drafts"

    async def unrelated():
        await pilots.receive(_message("Четверг:\n17:00 Иванов Иван 45", uid=nazar), 1, edited=False)
        await pilots.receive(_message("Расписание Леры на четверг\n17:00 Иванов Иван 45", uid=nazar), 2, edited=False)
        await pilots.receive(_message("Четверг:\n18:00 Петрова Анна 90", uid=ivan), 3, edited=False)

    async def incoming():
        await pilots.receive(_message("Расписание:\n17:00 Иванов Иван 30", uid=nazar), 4, edited=False)
        post = "Расписание занятий на четверг\n18:00 Петрова Анна 90\n19:30 группа (начинающие)"
        await pilots.receive(_message(post, uid=ivan), 5, edited=False)

    asyncio.run(unrelated())
    for teacher in (nazar, ivan):
        assert call_teacher(app, "GET", route, tg_id=teacher) == (
            200, {
                "drafts": [], "today": "2026-11-19", "completedToday": False, "upcomingDates": [],
                "acceptWeekday": False, "acceptPlainTitle": True, "acceptWeekly": False, "acceptOwnerInitials": False,
            },
        )
    asyncio.run(incoming())
    view_nazar = call_teacher(app, "GET", route, tg_id=nazar)[1]["drafts"][0]
    view_ivan = call_teacher(app, "GET", route, tg_id=ivan)[1]["drafts"][0]
    assert [s["time"] for s in view_nazar["slots"]] == ["17:00"]
    assert view_nazar["slots"][0]["studentIds"] == []
    assert [s["time"] for s in view_ivan["slots"]] == ["18:00", "19:30"]
    assert view_ivan["slots"][0]["studentIds"] == ["STU-0002"]
    assert view_ivan["slots"][1]["kind"] == "group"
    assert view_ivan["slots"][1]["text"] == "группа (начинающие)"
    assert call_teacher(app, "GET", "/api/teacher/home", tg_id=nazar)[1]["schedulePilot"] is True
    assert call_teacher(app, "GET", "/api/teacher/home", tg_id=ivan)[1]["schedulePilot"] is True

    slot = view_nazar["slots"][0]
    assert slot["duration"] == 30
    assert call_teacher(app, "PUT", route + "/17", tg_id=nazar, json=view_nazar)[0] == 200
    updated = call_teacher(app, "GET", route, tg_id=nazar)[1]["drafts"][0]
    updated["slots"][0]["duration"] = 75
    assert call_teacher(app, "PUT", route + "/17", tg_id=nazar, json=updated)[0] == 400
    updated["slots"][0]["duration"] = 60
    assert call_teacher(app, "PUT", route + "/17", tg_id=nazar, json=updated)[0] == 200


def test_weekly_post_keeps_each_days_edits_and_attendance_separate(monkeypatch, tmp_path):
    today = [date(2026, 11, 17)]
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: today[0])
    app, dp = make_api(monkeypatch)
    db = tmp_path / "week.db"
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", db, accept_plain_title=True, accept_weekly=True)
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    path = "/api/teacher/schedule-drafts"
    post = ("Добрый день! Расписание со мной на эту неделю\n"
            "ВТОРНИК\n17:15 Иванов Иван (60)\n18:15\n"
            "СРЕДА\n17:15 Петрова Анна\n18:00 группа (начинающие)\n18:45")

    def current():
        return call_teacher(app, "GET", path, tg_id=ADMIN_TG)[1]

    asyncio.run(pilot.receive(_message(post.replace("со мной", "Леры")), 1, edited=False))
    assert current()["drafts"] == []
    asyncio.run(pilot.receive(_message(post), 2, edited=False))
    tue_resp = current()
    assert len(tue_resp["drafts"]) == 1 and tue_resp["upcomingDates"] == ["2026-11-18"]
    tuesday = tue_resp["drafts"][0]
    today[0] = date(2026, 11, 18)
    wed_resp = current()
    assert len(wed_resp["drafts"]) == 1 and wed_resp["upcomingDates"] == []
    wednesday = wed_resp["drafts"][0]
    assert (tuesday["date"], wednesday["date"]) == ("2026-11-17", "2026-11-18")
    assert tuesday["id"] != wednesday["id"]
    assert tuesday["source"] == wednesday["source"] == "https://t.me/c/1111111111/17"
    assert [(s["duration"], s["suggested"], s["kind"]) for s in tuesday["slots"]] == [
        (60, False, "individual"), (None, False, "free"),
    ]
    assert [(s["duration"], s["kind"]) for s in wednesday["slots"]] == [
        (45, "individual"), (45, "group"), (None, "free"),
    ]
    tuesday["slots"][0]["duration"] = 90
    assert call_teacher(app, "PUT", f'{path}/{tuesday["id"]}', tg_id=ADMIN_TG, json=tuesday)[0] == 200
    marked = {**wednesday, "actions": [{"time": "17:15", "status": "cancelled"}]}
    before = list(dp["lesson_repo"].items)
    assert call_teacher(app, "POST", f'{path}/{wednesday["id"]}/attendance', tg_id=ADMIN_TG, json=marked)[0] == 200
    assert dp["lesson_repo"].items == before

    revised = post.replace("17:15 Иванов", "17:00 Иванов").split("СРЕДА")[0]
    asyncio.run(pilot.receive(_message(revised, edited=True), 3, edited=True))
    asyncio.run(pilot.receive(_message(post), 2, edited=False))
    today[0] = date(2026, 11, 17)
    tuesday = current()["drafts"][0]
    today[0] = date(2026, 11, 18)
    wednesday = current()["drafts"][0]
    assert tuesday["slots"][0]["time"] == "17:15" and tuesday["slots"][0]["duration"] == 90
    assert tuesday["sourceUpdated"] and tuesday["telegram"]["slots"][0]["time"] == "17:00"
    assert wednesday["sourceUpdated"] and "telegram" not in wednesday
    assert wednesday["slots"][0]["status"] == "cancelled"


def test_saved_teacher_draft_survives_telegram_edit_and_explicit_adoption(monkeypatch, tmp_path):
    today = [date(2026, 11, 24)]
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: today[0])
    app, dp = make_api(monkeypatch)
    lessons_before = list(dp["lesson_repo"].items)
    database = tmp_path / "drafts.db"
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", database)
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    dp["user_repo"].items.append(mk_user(tg_id=123, teacher_id="TCH-0001"))
    asyncio.run(pilot.receive(_message(TUESDAY), 1, edited=False))
    route = "/api/teacher/schedule-drafts"

    def current():
        _, data = call_teacher(app, "GET", route, tg_id=ADMIN_TG)
        return data["drafts"][0]

    original = current()
    assert original["id"] == 17 and original["revision"] == 0
    save = {**original, "date": "2026-11-25"}
    save["slots"][0].update(time="16:55", duration=60, studentIds=["STU-0001", "STU-0002"])
    save["slots"][3].update(time="19:30", groupId="GRP-0001")
    assert call_teacher(app, "PUT", route + "/17", tg_id=123, json=save)[0] == 404
    assert call_teacher(app, "PUT", route + "/17", tg_id=ADMIN_TG, json=save)[0] == 200
    today[0] = date(2026, 11, 25)

    updated = TUESDAY.replace("16.15 - Ира М.", "16.30 - Ира М.")
    asyncio.run(pilot.receive(_message(updated, edited=True), 2, edited=True))
    dp["schedule_pilots"] = SchedulePilots([SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", database)])
    edited = current()
    assert edited["date"] == "2026-11-25"
    assert edited["sourceUpdated"] is True
    assert [(s["time"], s["end"]) for s in edited["slots"] if s["telegramTime"] in ("16:15", "19:00")] == [
        ("16:55", "17:55"), ("19:30", "21:00"),
    ]
    assert edited["slots"][0]["students"] == ["Иванов Иван", "Петрова Анна"]
    assert next(s for s in edited["slots"] if s["time"] == "19:30")["text"] == "БП Джаз"
    assert edited["telegram"]["slots"][0]["time"] == "16:30"
    assert call_teacher(app, "PUT", route + "/17", tg_id=ADMIN_TG, json=save)[0] == 409  # второе окно с устаревшей версией

    adopt = {"revision": edited["revision"], "sourceRevision": edited["telegram"]["sourceRevision"], "useTelegram": True}
    asyncio.run(pilot.receive(_message(updated.replace("16.30", "16.45"), edited=True), 3, edited=True))
    assert call_teacher(app, "PUT", route + "/17", tg_id=ADMIN_TG, json=adopt)[0] == 409
    assert current()["slots"][0]["time"] == "16:55"
    adopt["sourceRevision"] = current()["telegram"]["sourceRevision"]
    assert call_teacher(app, "PUT", route + "/17", tg_id=ADMIN_TG, json=adopt)[0] == 200
    today[0] = date(2026, 11, 24)
    assert current()["slots"][0]["time"] == "16:45" and not current()["sourceUpdated"]

    asyncio.run(pilot.receive(_message("Влад\n16:45 Егор и Катя", edited=True), 4, edited=True))
    assert current()["slots"][0]["time"] == "16:45" and current()["sourceUpdated"]
    assert dp["lesson_repo"].items == lessons_before


def test_draft_does_not_accept_new_rows_or_convert_free_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: date(2026, 11, 24))
    app, dp = make_api(monkeypatch)
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", tmp_path / "only-telegram.db")
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    original = TUESDAY + "\n♦️ 21:15 свободно"
    asyncio.run(pilot.receive(_message(original), 1, edited=False))
    path = "/api/teacher/schedule-drafts"

    def read():
        return call_teacher(app, "GET", path, tg_id=ADMIN_TG)[1]["drafts"][0]

    d = read()
    lesson = {"time": "22:00", "kind": "individual", "studentIds": ["STU-0001"], "duration": 45}
    for bogus in (lesson, {**lesson, "telegramTime": "22:00"}, {**lesson, "telegramTime": "16:15"}):
        edit = {**d, "slots": [*d["slots"], bogus]}
        assert call_teacher(app, "PUT", path + "/17", tg_id=ADMIN_TG, json=edit)[0] == 400
    edit = {**d, "slots": [*d["slots"][:-1], {**lesson, "telegramTime": "21:15"}]}
    assert call_teacher(app, "PUT", path + "/17", tg_id=ADMIN_TG, json=edit)[0] == 400
    assert read()["slots"][-1]["kind"] == "free"

    # Старый клиент раньше мог создать ручную строку. Она не должна попасть в новый список или в журнал.
    stored = {"date": d["date"], "slots": [
        {"time": "16:15", "names": "Иванов Иван", "kind": "individual", "duration": 60, "suggested": False},
        {"time": "22:00", "names": "Петрова Анна", "kind": "individual", "duration": 45, "suggested": False},
    ]}
    with closing(sqlite3.connect(pilot.path)) as conn, conn:
        conn.execute("INSERT INTO draft_edits VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (CHAT, ADMIN_TG, "TCH-0001", 17, 1, d["sourceRevision"], json.dumps(stored)))
    assert [s["time"] for s in read()["slots"]] == ["16:15"]
    asyncio.run(pilot.receive(_message(original.replace("16.15", "16.30"), edited=True), 2, edited=True))
    assert [s["time"] for s in read()["slots"]] == ["16:15"]  # правка старой строки пережила изменение поста
    p = read()
    body = {**p, "actions": [{"time": "22:00", "status": "held"}]}
    assert call_teacher(app, "POST", path + "/17/attendance", tg_id=ADMIN_TG, json=body)[0] == 400


def test_day_result_records_group_bills_skips_cancelled_and_can_link_existing(monkeypatch, tmp_path):
    day = date.today()
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: day)
    app, dp = make_api(monkeypatch)
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", tmp_path / "day.db")
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    dp["user_repo"].items.append(mk_user(tg_id=123, teacher_id="TCH-0001"))
    published = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    text = "Расписание со мной\n17:00 Иванов Иван 45\n18:00 Петрова Анна 60\n19:00 группа 90\n20:30 Свободно"

    def receive(value, uid):
        msg = _message(value).model_copy(update={"date": published, "edit_date": int(published.timestamp()) + uid})
        asyncio.run(pilot.receive(msg, uid, edited=uid != 1))

    receive(text, 1)
    route = "/api/teacher/schedule-drafts"

    def call(method, suffix="", body=None, actor=ADMIN_TG):
        return call_teacher(app, method, route + suffix, tg_id=actor, json=body)

    def plan():
        return call("GET")[1]["drafts"][0]

    def actions(*items, actor=ADMIN_TG):
        p = plan()
        return call("POST", "/17/attendance",
                    {"revision": p["revision"], "sourceRevision": p["sourceRevision"], "actions": items}, actor=actor)

    p = plan()
    p["slots"][2]["groupId"] = "GRP-0001"
    assert call("PUT", "/17", p)[0] == 200
    teacher = await_teacher(dp)
    journal = dp["lesson_repo"].items
    journal[:] = [mk_lesson("LES-098877", teacher, day.isoformat(), duration=45,
                             students=[("STU-0001", "Иванов Иван")])]
    held = {"time": "19:00", "status": "held", "attendanceConfirmed": True, "studentIds": ["STU-0001"]}
    cancelled = {"time": "18:00", "status": "cancelled"}
    assert actions(held, actor=123)[0] == 404
    assert actions({"time": "19:00", "status": "held"})[1]["results"][0]["code"] == "attendance"
    status, result = actions(held, cancelled, {"time": "17:00", "status": "held"})
    assert status == 200
    group, cancellation, existing = result["results"]
    assert group["status"] == "held" and cancellation["status"] == "cancelled"
    assert existing["matches"][0]["id"] == "LES-098877"
    assert len(journal) == 2
    group_lesson = next(ls for ls in journal if ls.lesson_id == group["lessonId"])
    bills = build_billing_rows(group_lesson, teacher)
    assert [(b.student_id, b.amount) for b in bills] == [("STU-0001", 800)]

    assert actions(held)[1]["results"][0]["lessonId"] == group["lessonId"]
    linked = actions({"time": "17:00", "status": "held", "lessonId": "LES-098877"})[1]["results"][0]
    assert linked["lessonId"] == "LES-098877" and len(journal) == 2
    # Все занятия черновика внесены или отменены → черновик исчезает из активного списка.
    done_view = call("GET")[1]
    assert done_view["drafts"] == [] and done_view["completedToday"] is True
    home = call_teacher(app, "GET", "/api/teacher/home", tg_id=ADMIN_TG)[1]
    assert home["scheduleTodayPending"] == 0 and home["scheduleCompletedToday"] is True

    # Если пост в Telegram отредактировали и появилось новое время — черновик возвращается с пометкой обновления.
    receive(text.replace("17:00", "17:15"), 2)
    p = plan()
    assert p["sourceUpdated"] and p["slots"][0]["time"] == "17:00" and p["slots"][0]["status"] == "held"
    assert p["slots"][1]["status"] == "cancelled"
    applied = {"revision": p["revision"], "sourceRevision": p["telegram"]["sourceRevision"], "useTelegram": True}
    assert call("PUT", "/17", applied)[0] == 200
    assert next(s for s in plan()["slots"] if s["time"] == "18:00")["status"] == "cancelled"
    assert len(journal) == 2


def test_only_today_uncompleted_drafts_are_offered(monkeypatch, tmp_path):
    """Прошлые и будущие дни не предлагаются к сохранению; сегодняшний исчезает сразу после внесения."""
    today = [date(2026, 11, 23)]  # понедельник
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: today[0])
    app, dp = make_api(monkeypatch)
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", tmp_path / "today.db", accept_owner_initials=True)
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))

    # Пост за прошлую субботу (21.11), за сегодня (Пн 23.11) и за завтра (Вт 24.11)
    sat_msg = _message("С.А. Сб\n10:30 Иванов Иван 60").model_copy(
        update={"message_id": 10, "date": datetime(2026, 11, 21, 7, tzinfo=timezone.utc)},
    )
    mon_msg = _message("С.А. Пн\n17:00 Иванов Иван 45").model_copy(
        update={"message_id": 11, "date": datetime(2026, 11, 22, 19, tzinfo=timezone.utc)},
    )
    tue_msg = _message("С.А. Вт\n18:00 Петрова Анна 60").model_copy(
        update={"message_id": 12, "date": datetime(2026, 11, 23, 18, tzinfo=timezone.utc)},
    )
    asyncio.run(pilot.receive(sat_msg, 1, edited=False))
    asyncio.run(pilot.receive(mon_msg, 2, edited=False))
    asyncio.run(pilot.receive(tue_msg, 3, edited=False))

    route = "/api/teacher/schedule-drafts"
    view = call_teacher(app, "GET", route, tg_id=ADMIN_TG)[1]
    assert [d["date"] for d in view["drafts"]] == ["2026-11-23"]
    assert view["completedToday"] is False
    assert view["upcomingDates"] == ["2026-11-24"]

    # Вносим сегодняшнее занятие в журнал — расписание на понедельник исчезает.
    draft = view["drafts"][0]
    body = {"revision": draft["revision"], "sourceRevision": draft["sourceRevision"],
            "actions": [{"time": "17:00", "status": "held"}]}
    assert call_teacher(app, "POST", f"{route}/{draft['id']}/attendance", tg_id=ADMIN_TG, json=body)[0] == 200
    after = call_teacher(app, "GET", route, tg_id=ADMIN_TG)[1]
    assert after["drafts"] == [] and after["completedToday"] is True
    assert after["upcomingDates"] == ["2026-11-24"]

    # Наступает вторник 24.11 — автоматически появляется расписание на вторник.
    today[0] = date(2026, 11, 24)
    tue_view = call_teacher(app, "GET", route, tg_id=ADMIN_TG)[1]
    assert [d["date"] for d in tue_view["drafts"]] == ["2026-11-24"]
    assert tue_view["completedToday"] is False
    assert tue_view["upcomingDates"] == []


def test_retry_after_journal_write_and_lost_reply_has_no_duplicate(monkeypatch, tmp_path):
    day = date.today()
    monkeypatch.setattr(schedule_pilot, "moscow_today", lambda: day)
    app, dp = make_api(monkeypatch)
    database = tmp_path / "retry.db"
    pilot = SchedulePilot(CHAT + 1, ADMIN_TG, "TCH-0001", database)
    dp["schedule_pilots"] = SchedulePilots([pilot])
    asyncio.run(dp["user_repo"].update_teacher_id(ADMIN_TG, "TCH-0001"))
    published = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    msg = _message("Расписание со мной\n17:00 Иванов Иван 45", chat=CHAT + 1).model_copy(update={"date": published})
    asyncio.run(pilot.receive(msg, 1, edited=False))
    repo = dp["lesson_repo"]
    repo.items.clear()
    original_add = repo.add_new

    async def broken_reply(lesson):
        await original_add(lesson)
        raise OSError("lost reply after writing")

    monkeypatch.setattr(repo, "add_new", broken_reply)
    route = "/api/teacher/schedule-drafts"

    def mark_held():
        _, view = call_teacher(app, "GET", route, tg_id=ADMIN_TG)
        draft = view["drafts"][0]
        body = {"revision": draft["revision"], "sourceRevision": draft["sourceRevision"],
                "actions": [{"time": "17:00", "status": "held"}]}
        return call_teacher(app, "POST", route + "/17/attendance", tg_id=ADMIN_TG, json=body)[1]["results"][0]

    assert mark_held()["status"] == "pending"
    assert len(repo.items) == 1
    dp["schedule_pilots"] = SchedulePilots([SchedulePilot(CHAT + 1, ADMIN_TG, "TCH-0001", database)])
    assert mark_held()["lessonId"] == repo.items[0].lesson_id
    assert len(repo.items) == 1
    assert call_teacher(app, "GET", route, tg_id=ADMIN_TG)[1]["drafts"] == []


def test_sqlite_uses_wal_and_drafts_reads_in_single_connection(monkeypatch, tmp_path):
    """SQLite работает в режиме WAL, а drafts() читает черновики и их результаты за одно открытие БД (без N+1)."""
    import sqlite3

    database = tmp_path / "wal.db"
    pilot = SchedulePilot(CHAT, ADMIN_TG, "TCH-0001", database)
    for mid in (101, 102, 103):
        msg = _message("Расписание со мной\n17:00 Иванов Иван 45", chat=CHAT).model_copy(update={"message_id": mid})
        asyncio.run(pilot.receive(msg, mid, edited=False))
        asyncio.run(pilot.set_outcome(mid, "17:00", "held", f"LES-{mid}"))

    with sqlite3.connect(database) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"

    opens = 0
    orig_open = pilot._open

    def counting_open():
        nonlocal opens
        opens += 1
        return orig_open()

    monkeypatch.setattr(pilot, "_open", counting_open)
    drafts = asyncio.run(pilot.drafts([]))
    assert len(drafts) == 3
    assert all(d["slots"][0]["status"] == "held" for d in drafts)
    assert opens == 1
