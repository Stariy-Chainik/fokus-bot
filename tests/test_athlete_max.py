"""Кабинет спортсмена в мини-приложении MAX (решение владельца 13.10.2026): вход по подписи MAX (тот же алгоритм,
что у Telegram, ключ — токен бота MAX), привязка прямо в приложении, задания и оценки — в MAX."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_admin_api, register_athlete_api
from bot.services.diary_service import DiaryService
from bot.services.parent_notifier import notify_athlete
from config.settings import settings
from tests.fakes import AthleteTaskRepoFake, FakeBot, TrainingEntryRepoFake
from tests.test_admin_api import make_api
from tests.test_telegram_auth import make_init_data

MAX_TOKEN = "max-test-token"
MAX_ID = 90001


def _max(user_id=MAX_ID, token=MAX_TOKEN):
    return {"Authorization": f"max {make_init_data(token=token, user_id=user_id)}"}


def _call(dp, method, path, headers, json=None, bot=None, admin=False):
    async def run():
        app = web.Application()
        (register_admin_api if admin else register_athlete_api)(app, dp, bot)
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
    dp, _ = make_api(monkeypatch)
    monkeypatch.setattr(settings, "max_bot_token", MAX_TOKEN)
    monkeypatch.setattr(settings, "athlete_group_ids", "GRP-0001")
    dp["entry_repo"], dp["task_repo"] = TrainingEntryRepoFake([]), AthleteTaskRepoFake()
    dp["diary_service"] = DiaryService(dp["entry_repo"], dp["task_repo"], dp["student_repo"],
                                       dp["student_group_repo"], dp["group_repo"], dp["visibility"])
    return dp


def test_max_user_joins_and_gets_in(api):
    dp = api
    status, r = _call(dp, "GET", "/api/athlete/me", _max())
    assert status == 403 and r["canJoin"] is True                        # не привязан — экран «Найдите себя»
    status, r = _call(dp, "GET", "/api/athlete/join", _max())
    assert status == 200 and [s["name"] for s in r["students"]] == ["Иванов Иван", "Петрова Анна"]
    assert _call(dp, "GET", "/api/athlete/join?q=пет", _max())[1]["students"] == [{"id": "STU-0002", "name": "Петрова Анна"}]

    bot = FakeBot()
    status, r = _call(dp, "POST", "/api/athlete/join", _max(), json={"studentId": "STU-0002"}, bot=bot)
    assert status == 200 and r["name"] == "Петрова Анна"
    assert dp["student_repo"].items[1].athlete_max_id == MAX_ID and dp["student_repo"].items[1].is_athlete
    assert any("Новый спортсмен в MAX" in str(m) for m in bot.sent)          # администраторам — с «Отвязать»
    status, me = _call(dp, "GET", "/api/athlete/me", _max())
    assert status == 200 and me["student"]["id"] == "STU-0002"

    assert _call(dp, "POST", "/api/athlete/join", _max(), json={"studentId": "STU-0001"})[1]["error"] == "already"
    status, r = _call(dp, "POST", "/api/athlete/join", _max(user_id=90002), json={"studentId": "STU-0002"}, bot=bot)
    assert status == 409 and r["error"] == "taken"                         # чужой аккаунт MAX не перехватит
    assert _call(dp, "POST", "/api/athlete/join", _max(user_id=90002), json={"studentId": "STU-0404"})[0] == 404


def test_max_signature_and_scope(api):
    dp = api
    assert _call(dp, "GET", "/api/athlete/me", _max(token="чужой"))[0] == 401          # подпись не тем токеном
    tg = {"Authorization": f"tma {make_init_data(user_id=MAX_ID)}"}
    assert _call(dp, "GET", "/api/athlete/join", tg)[0] == 401                          # привязка — только из MAX
    assert _call(dp, "GET", "/api/admin/me", _max(), admin=True)[0] == 401              # остальные кабинеты — Telegram


def test_notify_athlete_goes_to_both_messengers():
    class Notifier:
        def __init__(self):
            self.sent = []

        async def send_many(self, addrs, text, rows=None):
            self.sent.append((list(addrs), text))
            return len(self.sent[-1][0])

    from tests.fakes import mk_student
    s = mk_student("STU-1", "Иванов Иван")
    s.athlete_tg_id, s.athlete_max_id = 1, 2
    n = Notifier()
    assert asyncio.run(notify_athlete(s, "📋 задание", notifier=n)) == 2
    assert n.sent == [([("tg", 1), ("max", 2)], "📋 задание")]


def test_max_app_button_only_in_max():
    pytest.importorskip("maxapi")
    from bot.max.render import ME, to_max_markup
    from bot.screens.adapters import to_aiogram_markup
    from bot.screens.types import cb, maxapp
    ME.update(id=500314561096, username="id500314561096_bot")
    rows = [[maxapp("🏃 Открыть дневник спортсмена", "athlete")], [cb("Меню", "go:home")]]
    btn = to_max_markup(rows).payload.buttons[0][0]
    assert (btn.type.value, btn.web_app, btn.contact_id, btn.payload) == (
        "open_app", "id500314561096_bot", 500314561096, "athlete")      # webApp обязателен для MAX API
    tg = to_aiogram_markup(rows)
    assert [[b.text for b in row] for row in tg.inline_keyboard] == [["Меню"]]       # в Telegram кнопки нет


def test_max_bot_shows_athlete_screen(monkeypatch):
    pytest.importorskip("maxapi")
    from bot.max.handlers import start
    from tests.fakes import StudentRepoWritable, mk_student
    shown = []

    async def fake_send(bot, uid, text, rows=None, media=None):
        shown.append((uid, text, [[b.kind for b in row] for row in rows or []]))
    monkeypatch.setattr(start, "send_screen", fake_send)
    s = mk_student("STU-0001", "Иванов Иван")
    s.athlete_max_id = MAX_ID
    repo = StudentRepoWritable([s])
    monkeypatch.setattr(settings, "max_miniapp_enabled", False)
    assert asyncio.run(start._athlete_entry(None, MAX_ID, "", repo)) is False           # выключено — как раньше
    monkeypatch.setattr(settings, "max_miniapp_enabled", True)
    assert asyncio.run(start._athlete_entry(None, MAX_ID, "", repo)) is True            # привязанный спортсмен
    assert asyncio.run(start._athlete_entry(None, 777, "athlete", repo)) is True        # ссылка ?start=athlete
    assert asyncio.run(start._athlete_entry(None, 777, "", repo)) is False              # обычный родитель
    assert "Иванов Иван" in shown[0][1] and shown[0][2] == [["maxapp"]] and "найдите себя" in shown[1][1]


def test_send_screen_retries_without_app_button():
    """MAX отклонил кнопку мини-приложения (приложение не подключено) — сообщение уходит без неё."""
    pytest.importorskip("maxapi")
    from bot.max.render import send_screen
    from bot.screens.types import cb, maxapp

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, user_id, text, attachments=None):
            kinds = [getattr(b, "type", None) for a in attachments or [] for row in a.payload.buttons for b in row]
            if any(getattr(k, "value", k) == "open_app" for k in kinds):
                raise RuntimeError("open_app not allowed")
            self.sent.append((user_id, text, [getattr(k, "value", k) for k in kinds]))

    bot = Bot()
    asyncio.run(send_screen(bot, 1, "Меню", [[maxapp("📱 Открыть кабинет", "parent")], [cb("💳 Оплата", "client:my_bills")]]))
    assert bot.sent == [(1, "Меню", ["callback"])]


def test_client_log_accepts_beacon_without_auth(monkeypatch):
    from bot.api import register_miniapp_api

    def make_api_dp():
        return make_api(monkeypatch)[0]

    async def run():
        app = web.Application()
        register_miniapp_api(app, make_api_dp(), None)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/api/client-log", data='{"kind": "error", "msg": "x is not defined", "line": 12}')
            bad = await client.post("/api/client-log", data="не json")
            return resp.status, bad.status
        finally:
            await client.close()
    assert asyncio.run(run()) == (204, 204)
