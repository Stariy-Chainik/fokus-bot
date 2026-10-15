"""Задание группе в кабинете педагога (решение владельца 15.10.2026): всей группе или выбранным,
оценка за выполнение каждому; родителю — сообщения и «Дневник» в кабинете."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_parent_api
from bot.models.entities import AthleteTask
from bot.repositories.athlete_task_repo import _row_to_task, _task_to_row
from tests.test_admin_api import PARENT_TG
from tests.test_telegram_auth import make_init_data
from tests.test_teacher_api import _call, api  # noqa: F401 — фикстура педагога с дневником

HEADERS = ["task_id", "student_id", "teacher_id", "exercise", "minutes", "comment", "source", "created_at",
           "status", "closed_at", "batch_id", "group_id", "grade", "grade_comment", "graded_at"]


def _parent(dp, method, path):
    async def run():
        app = web.Application()
        register_parent_api(app, dp)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers={"Authorization": f"tma {make_init_data(user_id=PARENT_TG)}"})
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


def test_row_roundtrip_keeps_homework_fields():
    t = AthleteTask(task_id="TK-000001", student_id="STU-0001", teacher_id="TCH-0001", exercise="Шпагат", minutes=0,
                    batch_id="HW-000001", group_id="GRP-0001", grade=5, grade_comment="молодец", graded_at="2026-10-16")
    row = _task_to_row(t)
    assert len(row) == len(HEADERS)
    back = _row_to_task(dict(zip(HEADERS, row, strict=True)))
    assert (back.batch_id, back.group_id, back.grade, back.grade_comment, back.minutes) == (
        "HW-000001", "GRP-0001", 5, "молодец", 0)
    # старая строка без новых колонок читается как раньше
    old = _row_to_task(dict(zip(HEADERS[:10], _task_to_row(t)[:10], strict=True)))
    assert (old.batch_id, old.grade) == ("", 0)


def test_whole_group_and_selected_and_grade(api):  # noqa: F811
    app, dp = api
    # всей группе: оба ученика GRP-0001; минуты необязательны
    st, r = _call(app, "POST", "/api/teacher/homework",
                  json={"groupId": "GRP-0001", "studentIds": ["STU-0001", "STU-0002"], "exercise": "Растяжка <шпагат>"})
    assert st == 200 and r["count"] == 2 and r["number"] == 1
    tasks = dp["task_repo"].items
    assert {t.student_id for t in tasks} == {"STU-0001", "STU-0002"} and len({t.batch_id for t in tasks}) == 1
    sent = dp["notifier"].sent
    assert any(addrs == [("tg", PARENT_TG)] and "домашнее задание" in text and "&lt;шпагат&gt;" in text
               for addrs, text in sent)                                   # родителю Иванова, имя экранировано
    assert any(("tg", 777001) in addrs and "Домашнее задание" in text for addrs, text in sent)   # спортсмену

    # выбранным: только Петровой; чужой ученик и чужая группа отсекаются
    st, r2 = _call(app, "POST", "/api/teacher/homework",
                   json={"groupId": "GRP-0001", "studentIds": ["STU-0002", "STU-9999"], "exercise": "Связка", "minutes": 15})
    assert st == 200 and r2["count"] == 1 and r2["id"] != r["id"] and r2["number"] == 2   # №2 у этой группы
    assert _call(app, "POST", "/api/teacher/homework",
                 json={"groupId": "GRP-0404", "studentIds": ["STU-0001"], "exercise": "x"})[0] == 404
    assert _call(app, "POST", "/api/teacher/homework", json={"groupId": "GRP-0001", "studentIds": [], "exercise": "x"})[0] == 400
    assert _call(app, "POST", "/api/teacher/homework", json={"groupId": "GRP-0001", "studentIds": ["STU-0001"], "exercise": " "})[0] == 400

    st, lst = _call(app, "GET", "/api/teacher/homework?gid=GRP-0001")
    assert st == 200 and [(b["exercise"], b["total"], b["graded"]) for b in lst["batches"]] == [
        ("Связка", 1, 0), ("Растяжка <шпагат>", 2, 0)]                     # новые первыми
    assert lst["groups"] == [{"id": "GRP-0001", "name": "БП Джаз"}]
    assert [b["single"] for b in lst["batches"]] == ["Петрова Анна", ""]       # индивидуальное — видно, кому
    # из карточки ученика задание выдаётся от его группы
    assert _call(app, "GET", "/api/teacher/students/STU-0002")[1]["groupIds"] == ["GRP-0001"]

    st, b = _call(app, "GET", f"/api/teacher/homework/{r['id']}")
    assert st == 200 and [s["name"] for s in b["students"]] == ["Иванов Иван", "Петрова Анна"]
    tid = b["students"][0]["taskId"]
    st, g = _call(app, "POST", f"/api/teacher/homework/tasks/{tid}/grade", json={"grade": 5, "comment": "Чисто"})
    assert st == 200 and g["grade"] == 5
    task = next(t for t in dp["task_repo"].items if t.task_id == tid)
    assert (task.grade, task.status) == (5, "closed")
    # из сообщения ясно, за какое задание оценка: номер, дата выдачи, начало текста
    assert any(addrs == [("tg", PARENT_TG)] and "Задание №1 от" in text and "<b>5/5</b>" in text
               for addrs, text in dp["notifier"].sent)
    assert _call(app, "POST", f"/api/teacher/homework/tasks/{tid}/grade", json={"grade": 7})[0] == 400
    assert _call(app, "GET", f"/api/teacher/homework/{r['id']}")[1]["graded"] == 1

    # чужое задание педагогу не видно и не оценивается
    task.teacher_id = "TCH-0099"
    assert _call(app, "POST", f"/api/teacher/homework/tasks/{tid}/grade", json={"grade": 4})[0] == 404

    # родитель видит задание и оценку в «Дневнике», даже без кабинета спортсмена
    task.teacher_id = "TCH-0001"
    st, d = _parent(dp, "GET", "/api/parent/diary/STU-0001")
    assert st == 200 and [(h["exercise"], h["grade"], h["number"], h["scale"]) for h in d["homework"]] == [
        ("Растяжка <шпагат>", 5, 1, 5)]


def test_ten_point_scale_for_chosen_teacher(api, monkeypatch):  # noqa: F811
    """HOMEWORK_GRADE_SCALE=TCH-0011:10 — шкала 1–10 только у этого педагога (Яковлева), у остальных 1–5."""
    from config.settings import settings
    app, dp = api
    monkeypatch.setattr(settings, "homework_grade_scales", "TCH-0001:10")
    assert settings.homework_scale("TCH-0001") == 10 and settings.homework_scale("TCH-0002") == 5
    r = _call(app, "POST", "/api/teacher/homework", json={"groupId": "GRP-0001", "studentIds": ["STU-0001"], "exercise": "Шпагат"})[1]
    b = _call(app, "GET", f"/api/teacher/homework/{r['id']}")[1]
    assert b["scale"] == 10
    tid = b["students"][0]["taskId"]
    assert _call(app, "POST", f"/api/teacher/homework/tasks/{tid}/grade", json={"grade": 11})[0] == 400
    st, g = _call(app, "POST", f"/api/teacher/homework/tasks/{tid}/grade", json={"grade": 8})
    assert st == 200 and (g["grade"], g["scale"]) == (8, 10)
    assert any("<b>8/10</b>" in text for _addrs, text in dp["notifier"].sent)
    st, d = _parent(dp, "GET", "/api/parent/diary/STU-0001")
    assert [(h["grade"], h["scale"]) for h in d["homework"]] == [(8, 10)]


@pytest.mark.parametrize("grade", [0, 6])
def test_grade_out_of_range_rejected_by_service(api, grade):  # noqa: F811
    _app, dp = api
    with pytest.raises(ValueError):
        asyncio.run(dp["diary_service"].grade_task("TK-000001", grade))
