"""Автоподтверждение платежа опросом (bot/services/payment_watcher.py)."""
import asyncio
from types import SimpleNamespace

from bot.services.payment_watcher import _watch


class _Service:
    def __init__(self):
        self.confirmed = []

    async def confirm_period(self, student_id, period, by):
        self.confirmed.append((student_id, period))
        return 1

    async def record_payment(self, student_id, student_name, period, amount, by, teacher_ids=None, comment=None, payment_method=""):
        self.confirmed.append((student_id, period))
        self.amounts = getattr(self, "amounts", []) + [(amount, teacher_ids, payment_method)]
        return amount, 1


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


class _Users:
    async def get_admins(self):
        return [SimpleNamespace(tg_id=999)]


def _payment(status):
    return SimpleNamespace(
        status=status, amount=SimpleNamespace(value="850.00"),
        payment_method=SimpleNamespace(type="bank_card"),
    )


def test_watch_confirms_on_succeeded():
    statuses = iter(["pending", "pending", "succeeded"])

    async def fetch(pid):
        return _payment(next(statuses))

    svc, bot = _Service(), _Bot()
    asyncio.run(_watch("p1", "STU-1", "Пупкин Вася", "2026-09",
                       svc, bot, _Users(), parent_addr=("tg", 42),
                       interval=0, max_checks=10, fetch=fetch))
    assert svc.confirmed == [("STU-1", "2026-09")]
    assert svc.amounts == [(850, None, "yookassa_card")]
    assert {chat for chat, _ in bot.sent} == {42, 999}  # родитель и админ


def test_watch_stops_on_canceled():
    async def fetch(pid):
        return _payment("canceled")

    svc, bot = _Service(), _Bot()
    asyncio.run(_watch("p1", "STU-1", "Пупкин Вася", "2026-09",
                       svc, bot, _Users(), None, interval=0, max_checks=10, fetch=fetch))
    assert svc.confirmed == [] and bot.sent == []


def test_watch_survives_fetch_errors():
    calls = {"n": 0}

    async def fetch(pid):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("network")
        return _payment("succeeded")

    svc, bot = _Service(), _Bot()
    asyncio.run(_watch("p1", "STU-1", "Пупкин Вася", "2026-09",
                       svc, bot, _Users(), None, interval=0, max_checks=10, fetch=fetch))
    assert svc.confirmed == [("STU-1", "2026-09")]


def test_watch_spreads_a_multi_month_payment_by_months():
    """«Оплатить всё» через ЮКассу: сумма разносится по месяцам с самого раннего."""
    class _Svc(_Service):
        async def ledger_for(self, student, ym):
            rest = {"2026-09": 5000, "2026-10": 6000}[ym]
            return {"SUB": SimpleNamespace(remainder=rest)}

        async def record_payment_periods(self, student_id, student_name, periods, amount, by, comment=None, payment_method=""):
            from bot.services.payment_service import PaymentService
            return await PaymentService.record_payment_periods(self, student_id, student_name, periods, amount, by, comment, payment_method)

    async def fetch(pid):
        p = _payment("succeeded")
        p.amount = SimpleNamespace(value="11000.00")
        return p

    svc, bot = _Svc(), _Bot()
    asyncio.run(_watch("p2", "STU-1", "Пупкин Вася", "2026-09", svc, bot, _Users(), parent_addr=("tg", 42),
                       interval=0, max_checks=3, fetch=fetch, periods=["2026-10", "2026-09"]))
    assert svc.confirmed == [("STU-1", "2026-09"), ("STU-1", "2026-10")]
    assert [a for a, _t, _m in svc.amounts] == [5000, 6000]
    assert any("2026-09, 2026-10" in text for _c, text in bot.sent)
