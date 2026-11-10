"""Турниры ФТСАРР (решение владельца 10.11.2026): разбор открытого API результатов, привязка пар к ученикам бота
по фамилии и имени (уменьшительные, ё, номер сада), только клуб «Фокус» или наш тренер; лист competition_results
без дублей; обход с уведомлениями; блоки в карточках администратора, педагога, спортсмена и родителя."""
import asyncio

import pytest

from bot.repositories.base import BaseRepository
from bot.repositories.competition_repo import (
    HEADER, SCAN_HEADER, CompetitionResult, CompetitionResultRepository, CompetitionScanRepository,
)
from bot.services import competition_results as cr
from bot.services.ftsarr import Competition, parse_calendar_ids, parse_group_results, parse_schedule, program_of
from bot.services.diary_service import DiaryService
from config.settings import settings
from tests.fakes import AthleteTaskRepoFake, FakeSheetsClient, FakeWorksheet, TrainingEntryRepoFake, mk_student, mk_teacher
from tests.test_admin_api import ADMIN_TG, PARENT_TG, _call as admin_call, make_api
from tests.test_athlete_api import ATHLETE_TG, _call as athlete_call
from tests.test_parent_api import _call as parent_call
from tests.test_teacher_api import TEACHER_TG, _call as teacher_call


def _athlete(last, first, cls="E", pos=3, total=20):
    return {"athlete": {"fullName": {"lastName": last, "firstName": first, "patronymic": ""},
                        "link": {"athleteId": 1}}, "class": cls, "classPosition": {"position": pos, "total": total},
            "points": 1.5}


def _pair(number, place, a, b, club="Фокус Видное", city="Видное", coaches=("Река Станислав",)):
    return {"number": number, "place": place, "club": club, "city": city, "coaches": list(coaches),
            "athletes": [a, b]}


GROUP_JSON = {
    "group": "ВС. Юниоры-1, ST", "groupNumber": 7, "competitorType": "Couple",
    "competitors": [
        _pair(12, 1, _athlete("Хрящев", "Иван", "M", 1, 5), _athlete("Миненкова", "Александра", "S", 1, 16),
              club="Империя", city="Москва", coaches=("Фирстова Анна",)),
        _pair(31, 2, _athlete("Иванов", "Иван"), _athlete("Петрова", "Анна")),                 # оба в боте
        _pair(44, 3, _athlete("Иванов", "Иван"), _athlete("Чужая", "Маша"), club="Старт", coaches=("Ельникова Анна",)),
    ],
}
COMP = Competition(26877, "RUSSIAN OPEN", "Москва", "Крокус", "2026-10-17", "2026-10-19", True)


def test_parsers_and_program():
    html = '<a href="/common/calendar/competitions/26877/results">x</a> <a href="/common/calendar/competitions/26878/results">' \
           '</a> <a href="/common/calendar/competitions/26877/results">'
    assert parse_calendar_ids(html) == [26877, 26878]
    assert parse_schedule({"groups": [{"id": 5, "name": "ВС. Дети-2, LA", "competitorsCount": 40}]}) == [(5, "ВС. Дети-2, LA", 40)]
    g = parse_group_results(7, "x", GROUP_JSON)
    assert g.group_name == "ВС. Юниоры-1, ST" and len(g.competitors) == 3
    assert g.competitors[0].athletes[0].name == "Хрящев Иван" and g.competitors[0].athletes[1].dance_class == "S"
    assert g.competitors[1].coaches == ["Река Станислав"] and g.competitors[1].athletes[0].class_total == 20
    assert program_of("ВС. Юниоры-1, ST") == "ST" and program_of("ВС. Дети-2, LA") == "LA"
    assert program_of("ВС. Взрослые, Двоеборье") == "10" and program_of("World Cup Ten Dance") == "10"
    assert program_of("WDC AL U12 Ballroom") == "ST" and program_of("WDC AL U12 Latin") == "LA"
    assert program_of("ВФМ. Юниоры-2 до B класса, ST") == "ST" and program_of("Кубок клуба") == ""
    assert cr.months_back(2, __import__("datetime").date(2026, 1, 15)) == ["2026-01", "2025-12"]


def test_students_matched_by_name_with_diminutives_and_club():
    index = cr.StudentIndex([mk_student("STU-0001", "Васькин Саша"), mk_student("STU-0002", "Мадатов Коля"),
                             mk_student("STU-0003", "Осипова Соня"), mk_student("STU-0004", "Вергер Алексия 34"),
                             mk_student("STU-0005", "Дикая Эмилия"), mk_student("STU-0006", "Семёнов Данила")])
    assert index.find("Васькин", "Александр").student_id == "STU-0001"
    assert index.find("Мадатов", "Николай").student_id == "STU-0002"
    assert index.find("Осипова", "Софья").student_id == "STU-0003"
    assert index.find("Вергер", "Алексия").student_id == "STU-0004"            # номер сада в имени карточки
    assert index.find("Семенов", "Даниил").student_id == "STU-0006"            # ё и второе написание имени
    assert index.find("Дикая", "Эмилия").student_id == "STU-0005"
    assert index.find("Дикая", "Эмма") is None and index.find("Иванов", "Иван") is None
    teachers = cr.teacher_keys([mk_teacher("TCH-0001", "Река Станислав")])
    g = parse_group_results(7, "", GROUP_JSON)
    assert cr.is_ours(g.competitors[1], teachers) and not cr.is_ours(g.competitors[0], teachers)
    other = parse_group_results(7, "", {"competitors": [_pair(1, 1, _athlete("А", "Б"), _athlete("В", "Г"), club="Старт",
                                                                 coaches=("Река Станислав",))]})
    assert cr.is_ours(other.competitors[0], teachers)                           # чужой клуб, но наш тренер


def test_rows_from_group_only_for_bot_students_of_our_pairs():
    index = cr.StudentIndex([mk_student("STU-0001", "Иванов Иван"), mk_student("STU-0002", "Петрова Анна")])
    teachers = cr.teacher_keys([mk_teacher("TCH-0001", "Река Станислав")])
    rows = cr.rows_from_group(COMP, parse_group_results(7, "", GROUP_JSON), index, teachers)
    assert [(r.student_id, r.partner_id, r.partner_name, r.place, r.total) for r in rows] == [
        ("STU-0001", "STU-0002", "Петрова Анна", 2, 3), ("STU-0002", "STU-0001", "Иванов Иван", 2, 3)]
    r = rows[0]
    assert (r.competition_id, r.competition_name, r.date, r.city, r.group_name, r.program) == \
           (26877, "RUSSIAN OPEN", "2026-10-17", "Москва", "ВС. Юниоры-1, ST", "ST")
    assert (r.dance_class, r.class_position, r.class_total, r.coaches) == ("E", 3, 20, "Река Станислав")
    # пара Иванова из чужого клуба с чужим тренером (тёзка) — не наша
    assert all(r.place != 3 for r in rows)
    view = cr.view(rows, {"STU-0001": "Иванов Иван", "STU-0002": "Петрова Анна"})
    assert view[0]["partner"] == "Петрова Анна" and view[0]["partnerId"] == "STU-0002"
    comps = cr.competitions_view(rows, {"STU-0001": "Иванов Иван", "STU-0002": "Петрова Анна"})
    assert len(comps) == 1 and len(comps[0]["pairs"]) == 1                     # пара один раз
    pair = comps[0]["pairs"][0]
    assert [m["id"] for m in pair["members"]] == ["STU-0001", "STU-0002"] and pair["members"][1]["class"] == "E"
    text = cr.results_text(COMP, rows, {"STU-0001": "Иванов Иван", "STU-0002": "Петрова Анна"})
    assert text.count("Иванов Иван / Петрова Анна") == 1 and "2 из 3" in text and "(E: 3 из 20)" in text


def _clear():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    BaseRepository._parsed.clear()


def test_repository_upsert_has_no_duplicates_and_updates_places():
    _clear()
    ws = FakeWorksheet(HEADER, [])
    repo = CompetitionResultRepository(FakeSheetsClient(ws), "competition_results")
    row = CompetitionResult("", "STU-0001", 26877, "RUSSIAN OPEN", "2026-10-17", "Москва", "ВС. Юниоры-1, ST", "ST",
                            59, 180, "Дикая Эмилия", "STU-0005", "E", 3, 20, 1.5, "Река Станислав")
    added, updated = asyncio.run(repo.upsert_many([row]))
    assert [r.result_id for r in added] == ["CR-000001"] and updated == 0 and len(ws.rows) == 1
    assert ws.rows[0][15] == "1.50" and ws.rows[0][4] == "2026-10-17"
    assert asyncio.run(repo.upsert_many([row])) == ([], 0) and len(ws.rows) == 1         # повтор — ничего
    better = CompetitionResult(**{**row.__dict__, "place": 40, "class_position": 1})
    assert asyncio.run(repo.upsert_many([better])) == ([], 1) and len(ws.rows) == 1
    got = asyncio.run(repo.get_for_student("STU-0001"))
    assert got[0].place == 40 and got[0].class_position == 1 and got[0].points == 1.5
    scans = CompetitionScanRepository(FakeSheetsClient(FakeWorksheet(SCAN_HEADER, [])), "competition_scans")
    asyncio.run(scans.mark(26877, "RUSSIAN OPEN", "2026-10-17", "Москва", 47, 2))
    asyncio.run(scans.mark(26877, "RUSSIAN OPEN", "2026-10-17", "Москва", 47, 3))
    assert asyncio.run(scans.scanned_ids()) == {26877} and len(asyncio.run(scans.get_all())) == 1
    assert asyncio.run(scans.get_all())[0].hits == 3
    _clear()


class ClientStub:
    """Два турнира в календаре: завершённый с нашей парой и ещё идущий."""

    def __init__(self) -> None:
        self.calls: list = []
        self.closed = False

    async def calendar_ids(self, year, month, regions=None):
        self.calls.append(("calendar", year, month, list(regions or [])))
        return [26877, 26878, 26879]

    async def competition(self, cid):
        from datetime import date, timedelta
        self.calls.append(("competition", cid))
        if cid == 26877:
            return COMP
        if cid == 26879:                                 # закончился вчера, результаты ещё не выложены
            y = (date.today() - timedelta(days=1)).isoformat()
            return Competition(26879, "Кубок без результатов", "Видное", "", y, y, True)
        return Competition(26878, "Невинномысск", "Невинномысск", "", "2099-11-05", "2099-11-08", False)

    async def schedule(self, cid):
        if cid == 26879:
            return []
        return [(7, "ВС. Юниоры-1, ST", 3), (8, "ВС. Юниоры-1, LA", 0)]

    async def group_results(self, cid, gid, name=""):
        self.calls.append(("results", cid, gid))
        if gid == 8:
            raise RuntimeError("boom")                   # одна группа не ломает обход
        return parse_group_results(gid, name, GROUP_JSON)

    async def close(self):
        self.closed = True


class BotStub:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


@pytest.fixture()
def dp(monkeypatch):
    dp, _ = make_api(monkeypatch)
    monkeypatch.setattr(settings, "miniapp_url", "https://example.test/app/")
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    dp["student_repo"].items[0].athlete_tg_id = ATHLETE_TG
    dp["entry_repo"] = TrainingEntryRepoFake([])
    dp["task_repo"] = AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    return dp


def test_sync_writes_rows_marks_scans_and_notifies_once(dp):
    client, bot = ClientStub(), BotStub()
    stats = asyncio.run(cr.sync(dp, ["2026-10"], [], client=client, bot=bot))
    assert (stats.competitions, stats.scanned, stats.pending, stats.skipped, stats.rows, stats.added) == (3, 1, 2, 0, 2, 2)
    assert ("calendar", 2026, 10, []) in client.calls and client.closed is False       # чужую сессию не закрываем
    rows = dp["competition_result_repo"].items
    assert {r.student_id for r in rows} == {"STU-0001", "STU-0002"} and rows[0].competition_id == 26877
    assert asyncio.run(dp["competition_scan_repo"].scanned_ids()) == {26877}       # без групп — не отмечен, проверим завтра
    # администратору и педагогу Река (тренер пары) — по одному сообщению
    assert sorted(chat for chat, _ in bot.sent) == sorted([ADMIN_TG, TEACHER_TG])
    assert "🏆 Результаты турнира «RUSSIAN OPEN»" in bot.sent[0][1] and "Иванов Иван / Петрова Анна" in bot.sent[0][1]
    # второй обход: турнир уже обойдён — пропущен, сообщений нет
    bot.sent.clear()
    stats2 = asyncio.run(cr.sync(dp, ["2026-10"], [], client=client, bot=bot))
    assert (stats2.skipped, stats2.scanned, stats2.added) == (1, 0, 0) and bot.sent == []
    # --force обходит заново, но строки не дублируются и новых нет — тишина
    stats3 = asyncio.run(cr.sync(dp, ["2026-10"], [], client=client, bot=bot, force=True))
    assert (stats3.scanned, stats3.added) == (1, 0) and len(dp["competition_result_repo"].items) == 2 and bot.sent == []


def test_cards_and_admin_screen_show_results(dp, monkeypatch):
    asyncio.run(cr.sync(dp, ["2026-10"], [], client=ClientStub(), bot=None))
    card = admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]
    assert len(card["competitions"]) == 1
    c = card["competitions"][0]
    assert (c["competition"], c["date"], c["group"], c["place"], c["total"], c["partner"], c["partnerId"]) == \
           ("RUSSIAN OPEN", "2026-10-17", "ВС. Юниоры-1, ST", 2, 3, "Петрова Анна", "STU-0002")
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]["competitions"][0]["partner"] == "Петрова Анна"
    assert athlete_call(dp, "GET", "/api/athlete/home")[1]["competitions"][0]["place"] == 2
    assert parent_call(dp, "GET", "/api/parent/diary/STU-0001", tg_id=PARENT_TG)[1]["competitions"][0]["class"] == "E"
    home = admin_call(dp, "GET", "/api/admin/home?ym=2026-10")[1]
    assert home["competitions"] == {"count": 1, "pairs": 1, "results": 2}
    status, screen = admin_call(dp, "GET", "/api/admin/competitions?ym=2026-10")
    assert status == 200 and screen["period"] == "2026-10" and screen["scanning"] is False and screen["lastScan"]
    assert [p["members"][0]["name"] for p in screen["competitions"][0]["pairs"]] == ["Иванов Иван"]
    assert admin_call(dp, "GET", "/api/admin/competitions?ym=2026-09")[1]["competitions"] == []
    assert admin_call(dp, "GET", "/api/admin/students/STU-0002")[1]["competitions"][0]["partner"] == "Иванов Иван"
    # «Обновить» — обход фоном; тот же запрос во время обхода — 409
    started = []

    async def fake_sync(dp_, months, *a, **kw):
        started.append(months)
    monkeypatch.setattr(cr, "sync", fake_sync)
    status, r = admin_call(dp, "POST", "/api/admin/competitions/sync?ym=2026-10")
    assert status == 200 and r["months"] == ["2026-10"]
    monkeypatch.setattr(cr, "sync_running", lambda: True)
    assert admin_call(dp, "POST", "/api/admin/competitions/sync")[0] == 409
