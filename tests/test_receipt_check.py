"""Сверка суммы в чеке с заявкой родителя (bot/services/receipt_check.py).

Случай Мазилиной 06.11.2026: заявка 7650 ₽ (весь остаток за месяц), чек на 4250 ₽ — педагог зачёл по заявке.
Теперь чек читается, при расхождении администраторам уходит сообщение с кнопкой «зачесть как в чеке»,
при совпадении — тишина.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from bot.repositories.pending_action_repo import KIND_RECEIPT, OPEN
from bot.services import receipt_check as rc
from bot.services.payment_methods import callback_code
from config.settings import settings
from tests.test_admin_inbox_api import PendingRepoFake

ADMINS = [100, 200]
STUDENT = ("STU-0079", "Мазилина Валерия")


class _Users:
    async def get_admins(self):
        return [SimpleNamespace(tg_id=t) for t in ADMINS]


class _Bot:
    """aiogram Bot: файл чека и отправка сообщений."""

    def __init__(self) -> None:
        self.sent: list = []
        self.downloads = 0

    async def get_file(self, file_id):
        return SimpleNamespace(file_path=f"photos/{file_id}.jpg")

    async def download_file(self, path):
        self.downloads += 1
        return io.BytesIO(b"\xff\xd8\xff\xe0" + b"jpeg-bytes")

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    for store in (rc._deps, rc._locks, rc._tasks):
        store.clear()
    repo, bot = PendingRepoFake(), _Bot()
    rc.setup(bot, repo, _Users())
    calls: list = []

    def recog(amount, date="06.11.2026"):
        async def _r(data, mime):
            calls.append((data, mime))
            return rc.ReceiptInfo(amount, date) if amount is not None else None
        monkeypatch.setattr(rc, "_recognizer", _r)

    yield SimpleNamespace(repo=repo, bot=bot, recog=recog, calls=calls)
    rc._deps.clear()


async def _add(repo, period: str, amount: int, file_id: str = "file-1"):
    return await repo.add(KIND_RECEIPT, *STUDENT, period, amount, "receipt_unknown", "m43485310", file_id, "photo")


async def _settle():
    while rc._tasks:
        await asyncio.gather(*list(rc._tasks))


def _buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_mismatch_alerts_admins_with_receipt_amount_button(env):
    env.recog(4250)

    async def main():
        a = await _add(env.repo, "2026-10", 7650)
        rc.schedule(a, delay=0)
        await _settle()
        return a

    a = asyncio.run(main())
    assert a.receipt_check == "diff:4250"
    assert (a.receipt_amount, a.receipt_match, a.receipt_group) == (4250, False, False)
    assert [chat for chat, *_ in env.bot.sent] == ADMINS
    _, text, kb = env.bot.sent[0]
    assert "В чеке: 4250 ₽" in text and "в заявке: 7650 ₽" in text and "Дата в чеке: 06.11.2026" in text
    assert "Мазилина Валерия · октябрь 2026" in text
    assert _buttons(kb) == [f"pact:{a.action_id}:4250:{callback_code('receipt_unknown')}", f"pnay:{a.action_id}"]
    assert env.bot.downloads == 1 and env.calls[0][1] == "image/jpeg"


def test_matching_receipt_is_silent(env):
    env.recog(7650)

    async def main():
        a = await _add(env.repo, "2026-10", 7650)
        rc.schedule(a, delay=0)
        await _settle()
        return a

    a = asyncio.run(main())
    assert a.receipt_check == "ok:7650" and a.receipt_match is True
    assert env.bot.sent == []


def test_receipt_for_several_months_is_compared_with_their_sum(env):
    """«Оплатить всё»: один чек, заявка на каждый месяц — сверяем с суммой заявок, читаем чек один раз."""
    env.recog(7650)

    async def main():
        a1 = await _add(env.repo, "2026-09", 3400)
        a2 = await _add(env.repo, "2026-10", 4250)
        rc.schedule(a1, delay=0)
        rc.schedule(a2, delay=0)
        await _settle()
        return a1, a2

    a1, a2 = asyncio.run(main())
    assert a1.receipt_check == "ok:7650:g" and a2.receipt_check == "ok:7650:g"
    assert a1.receipt_group and a1.receipt_amount == 7650 and a1.receipt_match is True
    assert env.bot.sent == [] and len(env.calls) == 1


def test_group_mismatch_alerts_once_without_button(env):
    env.recog(5000)

    async def main():
        a1 = await _add(env.repo, "2026-09", 3400)
        a2 = await _add(env.repo, "2026-10", 4250)
        rc.schedule(a1, delay=0)
        rc.schedule(a2, delay=0)
        await _settle()
        return a1, a2

    a1, a2 = asyncio.run(main())
    assert a1.receipt_check == "diff:5000:g" and a2.receipt_check == "diff:5000:g"
    assert [chat for chat, *_ in env.bot.sent] == ADMINS          # одно сообщение на администратора, не два
    _, text, kb = env.bot.sent[0]
    assert "в заявках: 7650 ₽ (3400 + 4250)" in text and "сентябрь 2026, октябрь 2026" in text
    assert kb is None


def test_unreadable_receipt_is_marked_and_silent(env):
    env.recog(None)

    async def main():
        a = await _add(env.repo, "2026-10", 7650)
        rc.schedule(a, delay=0)
        await _settle()
        return a

    a = asyncio.run(main())
    assert a.receipt_check == "unread" and a.receipt_amount is None and a.receipt_match is None
    assert env.bot.sent == []


def test_decided_request_is_not_checked(env):
    env.recog(4250)

    async def main():
        a = await _add(env.repo, "2026-10", 7650)
        await env.repo.claim(a.action_id, "done", 1)
        rc.schedule(a, delay=0)
        await _settle()
        return a

    a = asyncio.run(main())
    assert a.receipt_check == "" and env.bot.sent == [] and env.calls == []


def test_disabled_without_api_key(env, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    env.recog(4250)

    async def main():
        a = await _add(env.repo, "2026-10", 7650)
        rc.schedule(a, delay=0)
        assert not rc._tasks
        return a

    a = asyncio.run(main())
    assert a.receipt_check == "" and env.calls == [] and a.status == OPEN


def test_sniff_mime():
    assert rc.sniff_mime(b"%PDF-1.4 ...") == "application/pdf"
    assert rc.sniff_mime(b"\x89PNG\r\n") == "image/png"
    assert rc.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert rc.sniff_mime(b"\x00\x00\x00\x18ftypheic") == ""


def test_inbox_shows_receipt_check(monkeypatch):
    """В «Ждут решения» видно сумму из чека и расхождение — кабинет подставляет её в кнопку зачёта."""
    from tests.test_admin_api import make_api
    from tests.test_admin_inbox_api import _call

    dp, _ = make_api(monkeypatch)
    repo = PendingRepoFake()
    dp["pending_repo"] = repo

    async def prepare():
        a = await repo.add(KIND_RECEIPT, "STU-0001", "Иванов Иван", "2026-10", 7650, "receipt_unknown", "1", "f", "photo")
        a.receipt_check = "diff:4250"

    asyncio.run(prepare())
    status, d = _call(dp, "GET", "/api/admin/inbox")
    assert status == 200 and d["total"] == 1
    item = d["items"][0]
    assert (item["receiptAmount"], item["receiptMatch"], item["receiptGroup"]) == (4250, False, False)
