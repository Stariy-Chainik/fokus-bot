"""API Mini App: авторизация по initData, счета, создание платежа."""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_miniapp_api
from bot.models.enums import PaymentStatus
from bot.utils.dates import last_periods
from config.settings import settings
from tests.test_telegram_auth import TOKEN, make_init_data

PARENT_TG = 826576855
PERIOD = last_periods(1)[0]


class _StudentRepo:
    async def get_by_parent_tg_id(self, tg_id):
        if tg_id != PARENT_TG:
            return []
        return [SimpleNamespace(student_id="STU-0001", name="Пупкин Вася", client_id=None)]


class _PaymentRepo:
    async def get_by_student_and_period(self, student_id, period):
        return [SimpleNamespace(teacher_id="TCH-0001", status=PaymentStatus.PAID, total_amount=1000)]


class _PaymentService:
    def __init__(self):
        self.created = []

    async def compute_bills_for_student_period(self, student_id, period):
        if period != PERIOD:
            return {}
        return {
            "TCH-0001": {"name": "Оплаченный", "total": 1000, "items": []},
            "TCH-0009": {"name": "Контарева", "total": 700, "items": []},
        }

    async def get_or_create_invoices_for_student_period(self, student, period):
        return [SimpleNamespace(teacher_id="TCH-0001", status=PaymentStatus.PAID),
                SimpleNamespace(teacher_id="TCH-0009", status=PaymentStatus.PENDING)]

    async def create_yookassa_payment(self, student_id, student_name, period, total, **kwargs):
        self.created.append((student_id, period, total))
        return "https://yookassa.test/pay", "pay-test-1"


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setattr(settings, "bot_token", TOKEN)
    monkeypatch.setattr(settings, "yookassa_shop_id", "shop")
    monkeypatch.setattr(settings, "yookassa_secret_key", "secret")
    app = web.Application()
    service = _PaymentService()
    class _ClientRepo:
        async def get_by_id(self, client_id):
            return None
    dp = {"student_repo": _StudentRepo(), "payment_repo": _PaymentRepo(),
          "payment_service": service, "client_repo": _ClientRepo(), "user_repo": None}
    register_miniapp_api(app, dp)
    return app, service


def _call(app, method, path, headers=None, json=None):
    async def run():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers or {}, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


def _auth():
    return {"Authorization": "tma " + make_init_data(user_id=PARENT_TG)}


def test_bills_requires_auth(api):
    app, _ = api
    status, body = _call(app, "GET", "/api/me/bills")
    assert status == 401 and body["error"] == "unauthorized"


def test_bills_returns_unpaid_remainder(api):
    app, _ = api
    status, body = _call(app, "GET", "/api/me/bills", headers=_auth())
    assert status == 200
    bill = body["bills"][0]
    assert bill["studentId"] == "STU-0001" and bill["period"] == PERIOD
    assert bill["total"] == 1700 and bill["toPay"] == 700  # оплаченный педагог исключён
    statuses = {t["teacherId"]: t["status"] for t in bill["teachers"]}
    assert statuses == {"TCH-0001": "PAID", "TCH-0009": "UNPAID"}


def test_pay_creates_payment_for_unpaid_only(api):
    app, service = api
    status, body = _call(app, "POST", "/api/me/pay", headers=_auth(),
                         json={"studentId": "STU-0001", "periodMonth": PERIOD})
    assert status == 200
    assert body == {"confirmationUrl": "https://yookassa.test/pay", "amount": 700}
    assert service.created == [("STU-0001", PERIOD, 700)]


def test_pay_foreign_student_forbidden(api):
    app, _ = api
    headers = {"Authorization": "tma " + make_init_data(user_id=111)}
    status, body = _call(app, "POST", "/api/me/pay", headers=headers,
                         json={"studentId": "STU-0001", "periodMonth": PERIOD})
    assert status == 403
