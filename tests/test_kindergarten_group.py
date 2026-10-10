"""Группа ребёнка в детском саду (решение владельца 10.10.2026): у детей садовых групп школы; заполняет
родитель (кабинет, MAX) или педагог, видит и правит администратор; текст пишется в лист как есть."""
import asyncio
from types import SimpleNamespace

import pytest

from bot.models.enums import GroupBillingMode
from bot.repositories.base import BaseRepository
from bot.repositories.student_repo import StudentRepository
from bot.services.kindergarten import clean_value, is_kindergarten_group
from tests.fakes import FakeSheetsClient, FakeWorksheet, mk_group
from tests.test_admin_api import _call as admin_call, make_api
from tests.test_parent_api import _call as parent_call
from tests.test_teacher_api import TEACHER_TG, _call as teacher_call


def test_kindergarten_group_detection_and_value():
    assert is_kindergarten_group(mk_group(name="БП Сад 🪴")) and is_kindergarten_group(mk_group(name="ЮБ сад ХГ 16:00"))
    assert not is_kindergarten_group(mk_group(name="БП Джаз")) and not is_kindergarten_group(mk_group(name="Посадская"))
    assert clean_value("  7  б ") == "7 б" and clean_value("") == "" and clean_value("x" * 41) is None


def test_repository_writes_text_as_is():
    """«1-2» в таблице стало бы датой: значение уходит raw, а не USER_ENTERED."""
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    raws = []

    class Sheet(FakeWorksheet):
        def batch_update(self, data, raw=True, **kw):
            raws.append(raw)
            return super().batch_update(data, raw=raw, **kw)
    headers = ["student_id", "name", "partner_id", "group_id", "group_tier", "client_id", "parent_tg_ids",
               "kindergarten_group", "athlete_tg_id", "parent_max_ids"]
    ws = Sheet(headers, [["STU-0001", "Иванов Иван", "", "", "full", "", "", "", "", ""]])
    repo = StudentRepository(FakeSheetsClient(ws), "students")
    assert asyncio.run(repo.update_kindergarten_group("STU-0001", "1-2"))
    assert raws == [True] and ws.rows[0][7] == "1-2"
    assert asyncio.run(repo.get_by_id("STU-0001")).kindergarten_group == "1-2"
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()


@pytest.fixture()
def dp(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    dp["group_repo"].items.append(mk_group("GRP-0005", "БП Сад 🪴", billing_mode=GroupBillingMode.PER_VISIT, price_full=850))
    asyncio.run(dp["student_group_repo"].add("STU-0001", "GRP-0005"))                # Иванов — садовый, родитель PARENT_TG
    asyncio.run(dp["teacher_group_repo"].add("TCH-0001", "GRP-0005"))
    return dp


def test_parent_fills_kindergarten_group(dp):
    h = parent_call(dp, "GET", "/api/parent/home")[1]["children"][0]
    assert (h["kindergarten"], h["kgroup"]) == (True, "")                             # → «Укажите группу в саду»
    url = "/api/parent/children/STU-0001/kgroup"
    assert parent_call(dp, "PUT", url, json={"value": "x" * 41})[0] == 400
    assert parent_call(dp, "PUT", url, json={"value": " Солнышко "}) == (200, {"ok": True, "value": "Солнышко"})
    assert parent_call(dp, "GET", "/api/parent/me")[1]["children"][0]["kgroup"] == "Солнышко"
    assert parent_call(dp, "PUT", "/api/parent/children/STU-0002/kgroup", json={"value": "5"})[0] == 404   # чужой
    dp["student_group_repo"].rows = [r for r in dp["student_group_repo"].rows if r.group_id != "GRP-0005"]
    assert parent_call(dp, "PUT", url, json={"value": "5"})[0] == 400                 # больше не садовый


def test_teacher_sees_and_edits_in_kindergarten_group(dp):
    g = teacher_call(dp, "GET", "/api/teacher/groups/GRP-0005")[1]
    assert g["kindergarten"] is True and [(s["id"], s["kgroup"]) for s in g["students"]] == [("STU-0001", "")]
    card = teacher_call(dp, "GET", "/api/teacher/students/STU-0001")[1]
    assert (card["kindergarten"], card["kgroup"]) == (True, "")
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0001/kgroup", json={"value": "7"})[1]["value"] == "7"
    assert teacher_call(dp, "GET", "/api/teacher/groups/GRP-0005")[1]["students"][0]["kgroup"] == "7"
    assert teacher_call(dp, "GET", "/api/teacher/students/STU-0002")[1]["kindergarten"] is False
    assert teacher_call(dp, "PUT", "/api/teacher/students/STU-0002/kgroup", json={"value": "7"})[0] == 400


def test_admin_card_group_and_edit(dp):
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0001/kgroup", json={"value": "12"})[0] == 200
    card = admin_call(dp, "GET", "/api/admin/students/STU-0001")[1]
    assert (card["kindergarten"], card["kgroup"]) == (True, "12")
    g = admin_call(dp, "GET", "/api/admin/groups/GRP-0005")[1]
    assert g["kindergarten"] is True and g["members"][0]["kgroup"] == "12"
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0001/kgroup", json={"value": ""})[1]["value"] == ""
    assert admin_call(dp, "PUT", "/api/admin/students/STU-0404/kgroup", json={"value": "1"})[0] == 404


def test_max_parent_sets_kindergarten_group(dp):
    pytest.importorskip("maxapi")
    from bot.max.handlers import _common, kindergarten as mx
    from bot.max.states import MaxParentStates
    stu = dp["student_repo"].items[0]
    stu.parent_max_ids = [900003]
    _common.DEPS.clear()
    _common.DEPS.update({"group_repo": dp["group_repo"], "student_group_repo": dp["student_group_repo"]})

    class Ctx:
        def __init__(self):
            self.state, self.data = None, {}

        async def set_state(self, s):
            self.state = s

        async def update_data(self, **kw):
            self.data.update(kw)

        async def get_data(self):
            return dict(self.data)

        async def clear(self):
            self.state, self.data = None, {}
    screens, sent = [], []

    async def edit(text=None, attachments=None):
        screens.append(text)

    async def ack(notification=None):
        screens.append(f"alert:{notification}")

    async def send_message(user_id=None, text=None, attachments=None):
        sent.append(text)
    try:
        rows = asyncio.run(_common.max_menu_rows([stu]))
        assert ["client:kgroup"] in [[b.value for b in r] for r in rows]
        ctx = Ctx()
        ev = SimpleNamespace(callback=SimpleNamespace(payload="client:kgroup"), message=object(), edit=edit, ack=ack, bot=None)
        asyncio.run(mx.on_kgroup(ev, ctx, 900003, dp["student_repo"]))
        assert ctx.state == MaxParentStates.kgroup_value and "Сейчас: не указана" in screens[-1]
        msg = SimpleNamespace(message=SimpleNamespace(body=SimpleNamespace(text="  9 «Ромашка» ")),
                              bot=SimpleNamespace(send_message=send_message))
        asyncio.run(mx.on_kgroup_value(msg, ctx, 900003, dp["student_repo"]))
        assert ctx.state is None and stu.kindergarten_group == "9 «Ромашка»" and "9 «Ромашка»" in sent[-1]
        dp["student_group_repo"].rows = [r for r in dp["student_group_repo"].rows if r.group_id != "GRP-0005"]
        assert not any(b.value == "client:kgroup" for r in asyncio.run(_common.max_menu_rows([stu])) for b in r)
    finally:
        _common.DEPS.clear()
