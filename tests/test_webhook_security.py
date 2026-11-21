"""Проверки безопасности webhook Telegram: токен бота не попадает в URL и логи, используется secret_token."""
from __future__ import annotations

import asyncio
import logging
import re

import pytest

from bot import __main__ as main_mod
from config.settings import settings


def test_webhook_secret_derived_without_leaking_bot_token(monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123456:SECRET-BOT-TOKEN-XYZ")
    monkeypatch.setattr(settings, "webhook_secret", "")
    secret = main_mod._webhook_secret()
    assert settings.bot_token not in secret
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret)


def test_webhook_secret_respects_explicit_setting(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "custom_secret-123")
    assert main_mod._webhook_secret() == "custom_secret-123"


def test_run_webhook_uses_secret_token_and_never_logs_bot_token(monkeypatch, caplog):
    """Путь вебхука — /webhook, подпись — через secret_token, в логах и URL нет BOT_TOKEN."""
    from aiohttp import web

    monkeypatch.setattr(settings, "bot_token", "999999:TOP-SECRET-TOKEN")
    monkeypatch.setattr(settings, "webhook_url", "https://example.test")
    monkeypatch.setattr(settings, "webhook_secret", "wh_secret_abc")

    recorded: dict = {}

    class FakeBot:
        async def set_webhook(self, **kwargs):
            recorded["set_webhook"] = kwargs

        async def delete_webhook(self):
            recorded["deleted"] = True

    class FakeDP:
        workflow_data: dict = {}

        def resolve_used_update_types(self):
            return ["message"]

    class FakeHandler:
        def __init__(self, dispatcher, bot, secret_token=None):
            recorded["handler_secret"] = secret_token

        def register(self, app, path):
            recorded["handler_path"] = path

    class FakeSite:
        def __init__(self, runner, host, port):
            pass

        async def start(self):
            raise RuntimeError("stop-after-start")

    monkeypatch.setattr("aiogram.webhook.aiohttp_server.SimpleRequestHandler", FakeHandler)
    monkeypatch.setattr("aiogram.webhook.aiohttp_server.setup_application", lambda app, dp, bot: None)
    monkeypatch.setattr(main_mod, "_register_payment_webhook", lambda app, dp, bot: None)
    monkeypatch.setattr(main_mod, "_register_miniapp_api", lambda app, dp, bot: None)
    monkeypatch.setattr(web, "TCPSite", FakeSite)

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="stop-after-start"):
            asyncio.run(main_mod._run_webhook(FakeBot(), FakeDP()))

    assert recorded["set_webhook"]["url"] == "https://example.test/webhook"
    assert recorded["set_webhook"]["secret_token"] == "wh_secret_abc"
    assert recorded["handler_path"] == "/webhook"
    assert recorded["handler_secret"] == "wh_secret_abc"
    assert settings.bot_token not in recorded["set_webhook"]["url"]
    assert all(settings.bot_token not in rec.getMessage() for rec in caplog.records)
