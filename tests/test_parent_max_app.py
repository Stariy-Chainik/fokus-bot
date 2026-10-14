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


def test_max_cabinet_only_menu(monkeypatch):
    rows = menu_rows(platform="max", max_app=True, max_cabinet_only=True)
    assert [[(b.kind, b.label) for b in row] for row in rows] == [
        [("maxapp", "📱 Открыть кабинет")], [("cb", "➕ Добавить ребёнка")]]
    monkeypatch.setattr(settings, "max_parent_cabinet_only", True)
    monkeypatch.setattr(settings, "max_miniapp_enabled", False)
    assert settings.max_cabinet_only is False                       # без мини-приложения — кнопки бота остаются


def test_max_cabinet_only_middleware_and_notifications(monkeypatch):
    pytest.importorskip("maxapi")
    from types import SimpleNamespace as NS
    from bot.max import middlewares
    from bot.services.parent_notifier import ParentNotifier
    from bot.screens.types import cb
    from tests.fakes import StudentRepoWritable, mk_student
    monkeypatch.setattr(settings, "max_parent_cabinet_only", True)
    monkeypatch.setattr(settings, "max_miniapp_enabled", True)
    shown = []

    async def fake_send(bot, uid, text, rows=None, media=None):
        shown.append((uid, text, [[b.kind for b in row] for row in rows or []]))
    monkeypatch.setattr("bot.max.render.send_screen", fake_send)
    passed = []

    async def handler(event, data):
        passed.append(event)

    acks = []

    async def ack(notification=None):
        acks.append(notification)
    mw = middlewares.ParentCabinetOnlyMiddleware()
    kid = mk_student("STU-1", "Иванов Иван")
    kid.parent_max_ids = [5]
    data = {"max_uid": 5, "student_repo": StudentRepoWritable([kid])}
    old = NS(callback=NS(payload="client_bill:STU-1:2026-10"), bot=None, ack=ack)
    asyncio.run(mw(handler, old, dict(data)))
    assert not passed and acks and shown[-1][2] == [["maxapp"]]           # старая кнопка счёта — подсказка
    asyncio.run(mw(handler, NS(callback=NS(payload="client:add_child"), bot=None, ack=ack), dict(data)))
    assert len(passed) == 1                                                # «Добавить ребёнка» работает
    photo = NS(callback=None, message=NS(body=NS(attachments=[NS(type="image")])), bot=None)
    asyncio.run(mw(handler, photo, dict(data)))
    assert len(passed) == 1 and "Чек прикрепляется в кабинете" in shown[-1][1]
    sticker = NS(callback=None, message=NS(body=NS(attachments=[NS(type="sticker")])), bot=None)
    asyncio.run(mw(handler, sticker, dict(data)))
    assert len(passed) == 2                                                # стикер — не чек, проходит дальше

    sent = []

    async def fake_screen(bot, uid, text, rows=None, media=None):
        sent.append([[b.kind for b in row] for row in rows or []])
    monkeypatch.setattr("bot.max.render.send_screen", fake_screen)
    n = ParentNotifier(max_bot=object())
    asyncio.run(n.send(("max", 5), "✅ Оплата подтверждена", [[cb("🧾 Счёт", "client_bill:STU-1:2026-10")]]))
    assert sent == [[["maxapp"]]]                                          # в уведомлении — кнопка кабинета


def test_tg_cabinet_only_notification_gets_cabinet_button(monkeypatch):
    """Telegram, PARENT_CABINET_ONLY: в уведомлении кнопка счёта бота заменяется на кнопку кабинета."""
    from bot.services.parent_notifier import _tg_cabinet_rows, bills_rows
    monkeypatch.setattr(settings, "parent_cabinet_only", True)
    monkeypatch.setattr(settings, "miniapp_url", "https://example.org/app/")
    rows = _tg_cabinet_rows(bills_rows())
    assert [[(b.kind, b.value) for b in r] for r in rows] == [[("webapp", "https://example.org/app/")]]
    monkeypatch.setattr(settings, "parent_cabinet_only", False)
    assert _tg_cabinet_rows(bills_rows()) == bills_rows()                    # бот по-старому — кнопка бота


def test_self_unlink_drops_client_card_address(api):
    """«Это не мой ребёнок»: адрес снимается и с карточки клиента — иначе счета приходили бы дальше."""
    dp = api
    from bot.models import Client
    dp["client_repo"].items.append(Client("CLT-0001", "Мама", max_id=PARENT_MAX))
    dp["student_repo"].items[1].client_id = "CLT-0001"
    status, _ = _call(dp, "DELETE", "/api/parent/children/STU-0002", _max(), bot=FakeBot())
    assert status == 200 and dp["client_repo"].items[-1].max_id is None
