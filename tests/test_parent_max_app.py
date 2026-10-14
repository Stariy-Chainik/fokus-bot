"""Кабинет родителя в мини-приложении MAX (решение владельца 14.10.2026): тот же /api/parent/*, вход по подписи MAX,
родитель — по `students.parent_max_ids`; заявки, отвязка и «кто привязан» — с адресом MAX (`m<id>`)."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_parent_api
from bot.screens.parent_menu import menu_rows
from config.settings import settings
from tests.fakes import FakeBot
from tests.test_admin_api import YM, make_api
from tests.test_telegram_auth import make_init_data

MAX_TOKEN = "max-test-token"
PARENT_MAX = 70001


def _max(user_id=PARENT_MAX):
    return {"Authorization": f"max {make_init_data(token=MAX_TOKEN, user_id=user_id)}"}


def _call(dp, method, path, headers, json=None, bot=None):
    async def run():
        app = web.Application()
        register_parent_api(app, dp, bot)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.fixture()
def api(monkeypatch):
    from tests.test_admin_inbox_api import PendingRepoFake
    dp, _ = make_api(monkeypatch)
    monkeypatch.setattr(settings, "max_bot_token", MAX_TOKEN)
    dp["student_repo"].items[1].parent_max_ids = [PARENT_MAX]            # Петрова Анна — родитель в MAX
    dp["pending_repo"] = PendingRepoFake()
    return dp


def test_max_parent_sees_own_child_and_bills(api):
    dp = api
    status, me = _call(dp, "GET", "/api/parent/me", _max())
    assert status == 200 and me["platform"] == "max" and me["tgId"] is None
    assert [c["name"] for c in me["children"]] == ["Петрова Анна"]
    assert _call(dp, "GET", "/api/parent/bills", _max())[0] == 200
    assert _call(dp, "GET", "/api/parent/me", _max(user_id=70002))[0] == 403         # не родитель
    bad = {"Authorization": f"max {make_init_data(token='чужой', user_id=PARENT_MAX)}"}
    assert _call(dp, "GET", "/api/parent/me", bad)[0] == 401                         # подпись не тем токеном


def test_max_parent_cash_request_and_unlink(api):
    dp = api
    bot = FakeBot()
    status, r = _call(dp, "POST", "/api/parent/pay", _max(), json={"studentId": "STU-0002", "ym": YM, "method": "cash"},
                      bot=bot)
    assert status == 200 and r["ok"]
    a = dp["pending_repo"].items[0]
    assert (a.kind, a.student_id, a.parent_addr) == ("cash", "STU-0002", f"m{PARENT_MAX}")   # ответ уйдёт в MAX
    status, r = _call(dp, "DELETE", "/api/parent/children/STU-0002", _max(), bot=bot)
    assert status == 200 and dp["student_repo"].items[1].parent_max_ids == []
    assert any("MAX: 70001" in str(m) for m in bot.sent)


def test_max_menu_gets_cabinet_button_when_enabled():
    rows = menu_rows(platform="max", max_app=True)
    assert rows[0][0].kind == "maxapp" and rows[0][0].label == "📱 Открыть кабинет"
    assert all(b.kind != "maxapp" for row in menu_rows(platform="max") for b in row)    # выключено — как раньше
