"""Тесты верификации webhook ЮКассы (fix B4 из docs/FOUND_BUGS.md).

Телу webhook доверять нельзя: process_yookassa_event обязан брать из него только
object.id и перепроверять платёж через API (здесь — фейковый fetch_payment).
"""
import asyncio
from types import SimpleNamespace

from bot.handlers.client.payments import process_yookassa_event


def _run(coro):
    return asyncio.run(coro)


class _FakePaymentService:
    def __init__(self):
        self.confirmed: list[tuple[str, str]] = []

    async def confirm_period(self, student_id, period_month, confirmed_by_tg_id):
        self.confirmed.append((student_id, period_month))
        return 1


class _FakeUserRepo:
    async def get_admins(self):
        return []  # уведомления в этих тестах не проверяем


def _api_payment(status="succeeded", student_id="STU-0001", period="2026-07", amount="500.00"):
    """Объект платежа, как его возвращает API ЮКассы (усечённо)."""
    return SimpleNamespace(
        status=status,
        metadata={"student_id": student_id, "period_month": period},
        amount=SimpleNamespace(value=amount),
    )


def _event(payment_id="pay-123", meta=None):
    obj = {"id": payment_id}
    if meta is not None:
        obj["metadata"] = meta
    return {"event": "payment.succeeded", "object": obj}


def _fetch_returning(payment):
    async def fetch(payment_id):
        return payment
    return fetch


def test_succeeded_payment_confirms_period():
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    assert status == 200
    assert svc.confirmed == [("STU-0001", "2026-07")]


def test_forged_body_metadata_is_ignored_api_is_source_of_truth():
    """Подделка: в теле metadata на другого ученика, но API говорит про STU-0001."""
    svc = _FakePaymentService()
    forged = _event(meta={"student_id": "STU-9999", "period_month": "2026-01"})
    _run(process_yookassa_event(
        forged, svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    # подтверждён тот (student, period), что вернул API, а не тело запроса
    assert svc.confirmed == [("STU-0001", "2026-07")]


def test_payment_not_found_in_api_does_not_confirm():
    """Подделка: платёж с таким id в нашем магазине не существует."""
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(meta={"student_id": "STU-0001", "period_month": "2026-07"}),
        svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(None),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_not_succeeded_status_does_not_confirm():
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment(status="pending")),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_api_error_returns_500_for_retry():
    """Сетевая ошибка проверки → 500, чтобы ЮКасса повторила уведомление."""
    svc = _FakePaymentService()

    async def failing_fetch(payment_id):
        raise ConnectionError("network down")

    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=failing_fetch,
    ))
    assert status == 500
    assert svc.confirmed == []


def test_missing_object_id_is_ignored():
    svc = _FakePaymentService()
    event = {"event": "payment.succeeded", "object": {"metadata": {"student_id": "STU-1", "period_month": "2026-07"}}}
    status = _run(process_yookassa_event(
        event, svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_other_events_are_ignored_without_api_call():
    svc = _FakePaymentService()
    calls = []

    async def counting_fetch(payment_id):
        calls.append(payment_id)
        return _api_payment()

    status = _run(process_yookassa_event(
        {"event": "payment.canceled", "object": {"id": "pay-123"}},
        svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=counting_fetch,
    ))
    assert status == 200
    assert calls == []
    assert svc.confirmed == []


def test_missing_metadata_in_api_payment_does_not_confirm():
    svc = _FakePaymentService()
    payment = SimpleNamespace(status="succeeded", metadata=None, amount=SimpleNamespace(value="500.00"))
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(payment),
    ))
    assert status == 200
    assert svc.confirmed == []
