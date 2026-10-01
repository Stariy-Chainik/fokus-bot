"""Чек, присланный в MAX без шага «Прикрепить чек», попадает в очередь решений кабинета
(случай Манохиной 01.10.2026: чек ушёл только в личку администраторам, заявки не было)."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("maxapi")

from bot.max.handlers import payment as mx  # noqa: E402
from tests.test_admin_api import YM, make_api  # noqa: E402
from tests.test_admin_inbox_api import PendingRepoFake  # noqa: E402


def _run(monkeypatch, pending):
    dp, _ = make_api(monkeypatch)
    sent = []

    async def notify(tg_bot, user_repo, caption, rows, **kw):
        sent.append(rows)

    async def download(url):
        return b"img"

    monkeypatch.setattr(mx, "_notify_admins_tg", notify)
    student = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    bot = SimpleNamespace(download_bytes=download)
    ok = asyncio.run(mx._forward_unbound(bot, None, dp["user_repo"], dp["payment_service"], student, YM, 4800,
                                         "image", "http://x", "r.jpg", 777, pending))
    return ok, sent


def test_unbound_max_receipt_is_queued(monkeypatch):
    pending = PendingRepoFake()
    ok, sent = _run(monkeypatch, pending)
    assert ok and len(pending.items) == 1
    a = pending.items[0]
    assert (a.kind, a.student_id, a.period_month, a.amount, a.parent_addr) == ("receipt", "STU-0001", YM, 4800, "m777")
    callbacks = [b.value for row in sent[0] for b in row]
    assert any(str(c).startswith(f"pact:{a.action_id}:") for c in callbacks)


def test_unbound_max_receipt_without_queue_still_reaches_admins(monkeypatch):
    ok, sent = _run(monkeypatch, None)
    assert ok and sent
