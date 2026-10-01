"""Старая кнопка чека «Подтвердить» (с суммой) при нажатии на уже закрытый счёт ничего не зачитывает
(случай Манохиной 01.10.2026: счёт закрыт педагогом, а две копии кнопки в чате админа остались)."""
import asyncio

from bot.handlers.client.my_bills.payment import cb_receipt_confirm
from tests.fakes import FakeCallbackQuery
from tests.test_admin_api import ADMIN_TG, YM, make_api


def _press(dp, data):
    cb = FakeCallbackQuery(data, user_id=ADMIN_TG)

    async def edit_reply_markup(**_):
        pass

    async def edit_text(*_, **__):
        pass

    cb.message.edit_reply_markup = edit_reply_markup
    cb.message.edit_text = edit_text
    cb.message.caption, cb.message.text = None, "чек"
    asyncio.run(cb_receipt_confirm(cb, None, dp["payment_service"], dp["student_repo"], dp["client_repo"],
                                   SimpleCash(), None))
    return cb


class SimpleCash:
    _public_id = ""


def test_repeated_receipt_button_does_not_overpay(monkeypatch):
    dp, _ = make_api(monkeypatch)
    ps = dp["payment_service"]
    data = f"receipt_confirm:STU-0001:{YM}:4800:a"
    _press(dp, data)                                    # первое нажатие закрывает счёт
    first = len(asyncio.run(ps._payment_repo.get_all()))
    cb = _press(dp, data)                               # повтор — счёт уже оплачен
    assert len(asyncio.run(ps._payment_repo.get_all())) == first, "повторное нажатие записало переплату"
    assert cb.alerts and cb.alerts[-1][1] is True
