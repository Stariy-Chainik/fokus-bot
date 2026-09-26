"""Чек, присланный без шага «Прикрепить чек», попадает в очередь решений кабинета
так же, как обычный (случай Сталяровой 26.09.2026: чек был в чате, а в кабинете — нет)."""
import asyncio
from types import SimpleNamespace

from bot.handlers.client.my_bills.payment import on_unbound_receipt
from tests.fakes import FakeBot, FakeState
from tests.test_admin_api import PARENT_TG, YM, make_api
from tests.test_admin_inbox_api import PendingRepoFake


def test_free_form_receipt_is_queued_for_the_cabinet(monkeypatch):
    dp, _ = make_api(monkeypatch)
    pending, bot, answers = PendingRepoFake(), FakeBot(), []

    async def answer(text, reply_markup=None, **_):
        answers.append(text)

    message = SimpleNamespace(photo=[SimpleNamespace(file_id="FILE-1")], document=None,
                              from_user=SimpleNamespace(id=PARENT_TG), bot=bot, answer=answer)
    asyncio.run(on_unbound_receipt(message, None, FakeState(), dp["student_repo"], dp["payment_service"],
                                   dp["user_repo"], pending_repo=pending))

    # один неоплаченный счёт (Иванов, 4800 ₽ за текущий месяц) → строка очереди с файлом чека
    assert len(pending.items) == 1
    a = pending.items[0]
    assert (a.kind, a.student_id, a.period_month, a.amount) == ("receipt", "STU-0001", YM, 4800)
    assert (a.file_id, a.file_type, a.parent_addr) == ("FILE-1", "photo", str(PARENT_TG))
    # кнопки админу — по номеру решения: чат и кабинет закрывают одну и ту же строку
    assert bot.sent, "чек не ушёл администраторам"
    _chat, _caption, kb = bot.sent[0]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith(f"pact:{a.action_id}:4800:") for c in callbacks)
    assert f"pnay:{a.action_id}" in callbacks
    assert answers, "родителю не ответили"


def test_free_form_receipt_without_queue_still_reaches_admins(monkeypatch):
    """Лист очереди недоступен — чек всё равно уходит в чат со старыми кнопками."""
    dp, _ = make_api(monkeypatch)
    bot = FakeBot()

    async def answer(text, reply_markup=None, **_):
        pass

    message = SimpleNamespace(photo=[SimpleNamespace(file_id="FILE-2")], document=None,
                              from_user=SimpleNamespace(id=PARENT_TG), bot=bot, answer=answer)
    asyncio.run(on_unbound_receipt(message, None, FakeState(), dp["student_repo"], dp["payment_service"],
                                   dp["user_repo"], pending_repo=None))
    callbacks = [b.callback_data for row in bot.sent[0][2].inline_keyboard for b in row]
    assert any(c.startswith(f"receipt_confirm:STU-0001:{YM}:4800:") for c in callbacks)
