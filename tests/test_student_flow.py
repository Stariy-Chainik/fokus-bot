"""Движение учеников за месяц (решение владельца 16.10.2026): пришли в школу / ушли / переводы."""
from types import SimpleNamespace

from bot.models.entities import StudentGroup
from bot.services.student_flow import month_flow
from tests.test_admin_api import ADMIN_TG, YM, _call, make_api

YM_ = "2026-10"
GROUPS = {"GRP-0001": "БП Джаз", "GRP-0002": "ВБ ХГ Младшая 🤸", "GRP-0003": "ВБ ХГ Средняя"}
STUDENTS = {"STU-0001": "Новикова Аня", "STU-0002": "Старов Петя", "STU-0003": "Ушакова Лена",
            "STU-0004": "Переходов Миша", "STU-0005": "Двойнова Катя", "STU-0006": "Безгруппов Ваня",
            "STU-0007": "Остался Коля"}


def _ev(ts, text):
    return SimpleNamespace(ts=ts, kind="student", text=text, ref="")


def test_month_flow_classifies_new_gone_and_moves():
    rows = [
        StudentGroup("STU-0001", "GRP-0002", joined_period=YM_),                         # новая в школе
        StudentGroup("STU-0002", "GRP-0001", joined_period="2026-04"),                    # давно, ничего не менялось
        StudentGroup("STU-0003", "GRP-0002", joined_period="", left_period=YM_),          # ушла из школы с октября
        StudentGroup("STU-0004", "GRP-0002", joined_period="2026-09", left_period=YM_),   # перевод Младшая → Средняя
        StudentGroup("STU-0004", "GRP-0003", joined_period=YM_),
        StudentGroup("STU-0005", "GRP-0001", joined_period="2026-05"),                    # добавлена ещё в одну группу
        StudentGroup("STU-0005", "GRP-0002", joined_period=YM_),
        StudentGroup("STU-0007", "GRP-0002", joined_period="2026-05", left_period=YM_),   # ушёл из одной, остался в другой
        StudentGroup("STU-0007", "GRP-0003", joined_period="2026-05"),
        StudentGroup("STU-0008", "GRP-0002", joined_period="2026-11"),                    # следующий месяц — не считается
    ]
    events = [
        _ev(f"{YM_}-03 12:00:00", "Новый ученик: STU-0006 Безгруппов Ваня"),            # карточка без группы
        _ev(f"{YM_}-02 10:00:00", "Ученик STU-0001 добавлен в группу GRP-0002 с 2026-10"),   # день прихода
        _ev("2026-09-28 18:00:00", "Ученик STU-0003 уходит из группы GRP-0002 с 2026-10"),   # ушла «со следующего» — день в сентябре
        _ev(f"{YM_}-05 12:00:00", "Удалён ученик STU-0099"),                             # карточки больше нет
        _ev(f"{YM_}-06 12:00:00", "Удалён ученик STU-0097 Дублёва Маша"),                # новое событие — с именем
        _ev(f"{YM_}-07 12:00:00", "Ученик STU-0002 убран из группы GRP-0001"),           # по посещению: строка удалена
        _ev("2026-09-30 12:00:00", "Удалён ученик STU-0098"),                            # прошлый месяц — мимо
    ]
    rows = [r for r in rows if r.student_id != "STU-0002"] + [
        StudentGroup("STU-0002", "GRP-0003", joined_period="2026-04")]                    # Петя остался в Средней
    r = month_flow(YM_, rows, events, STUDENTS, GROUPS)
    assert [(x.name, x.groups, x.note, x.date) for x in r.new] == [                 # по дням
        ("Новикова Аня", ["ВБ ХГ Младшая 🤸"], "", f"{YM_}-02"), ("Безгруппов Ваня", [], "без группы", f"{YM_}-03")]
    assert [(x.name, x.groups, x.note, x.date) for x in r.gone] == [
        ("Ушакова Лена", ["ВБ ХГ Младшая 🤸"], "с октября", "2026-09-28")]
    assert r.as_dict()["new"][0]["date"] == f"{YM_}-02"
    # удалённые карточки — отдельно и не в «ушли»: на проде это чистка дублей
    assert [(x.name, x.note) for x in r.deleted] == [("Дублёва Маша", ""), ("STU-0099", "имя не сохранилось")]
    assert [(x.name, x.groups, x.note) for x in r.moves] == [
        ("Двойнова Катя", ["ВБ ХГ Младшая 🤸"], "ещё одна группа"),
        ("Остался Коля", ["ВБ ХГ Младшая 🤸"], "ушёл из группы, остался в ВБ ХГ Средняя"),
        ("Переходов Миша", ["ВБ ХГ Средняя"], "из ВБ ХГ Младшая 🤸"),
        ("Старов Петя", ["БП Джаз"], "ушёл из группы, остался в ВБ ХГ Средняя")]
    assert r.net == 1 and r.as_dict()["counts"] == {"new": 2, "gone": 1, "deleted": 2, "net": 1}
    assert "moves" not in r.as_dict()                       # переводы наружу не отдаются (16.10.2026)


def test_transfer_event_counts_per_visit_source_as_left():
    rows = [StudentGroup("STU-0004", "GRP-0003", joined_period=YM_)]        # из БП Джаз (по посещению) строки нет
    events = [_ev(f"{YM_}-10 10:00:00", "Ученик STU-0004 переведён педагогом Река: GRP-0001 → GRP-0003")]
    r = month_flow(YM_, rows, events, STUDENTS, GROUPS)
    assert not r.new and not r.gone and [(x.groups, x.note) for x in r.moves] == [(["ВБ ХГ Средняя"], "из БП Джаз")]


def test_home_and_flow_api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    rows = dp["student_group_repo"]
    for x in __import__("asyncio").run(rows.get_all()):
        if x.student_id == "STU-0002":
            x.joined_period = YM                         # Петрова пришла в этом месяце
    h = _call(dp, "GET", "/api/admin/home", tg_id=ADMIN_TG)[1]
    assert h["flow"] == {"new": 1, "gone": 0, "deleted": 0, "net": 1}
    st, d = _call(dp, "GET", f"/api/admin/flow?ym={YM}", tg_id=ADMIN_TG)
    assert st == 200 and [x["name"] for x in d["new"]] == ["Петрова Анна"] and d["counts"]["net"] == 1
    assert len(d["months"]) == 6 and d["months"][0] == {"ym": YM, "new": 1, "gone": 0, "deleted": 0, "net": 1}
    assert all(m["new"] == 0 for m in d["months"][1:])      # прошлые месяцы — без движения
    assert _call(dp, "GET", "/api/admin/flow?ym=2020-01", tg_id=ADMIN_TG)[1]["counts"]["net"] == 0
