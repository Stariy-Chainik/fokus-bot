"""Адреса родителей и доставка в оба мессенджера (bot/services/parent_notifier.py)."""
import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError

from bot.models import Student, Client
from bot.services.parent_notifier import (
    ParentNotifier, fmt_addr, parse_addr, addrs_of, tg_addr, max_addr,
)
from bot.screens import cb, url


def test_addr_format_backwards_compatible():
    assert fmt_addr(tg_addr(123)) == "123"          # как раньше — голые цифры
    assert fmt_addr(max_addr(456)) == "m456"
    assert parse_addr("123") == ("tg", 123)
    assert parse_addr("m456") == ("max", 456)
    assert parse_addr("abc") is None and parse_addr("") is None


def test_student_parent_addrs_and_addrs_of():
    s = Student("STU-1", "Иванов", parent_tg_ids=[1, 2], parent_max_ids=[9])
    assert s.parent_addrs == [("tg", 1), ("tg", 2), ("max", 9)]
    c = Client("CLT-1", "Мама", tg_id=1, max_id=7)
    assert addrs_of(s, c) == [("tg", 1), ("max", 7), ("tg", 2), ("max", 9)]  # клиент первым, без дублей
    assert addrs_of(s, None) == s.parent_addrs


class _TgBot:
    def __init__(self, fail_ids=()):
        self.sent, self.fail_ids = [], set(fail_ids)

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.fail_ids:
            raise TelegramAPIError(method=SimpleNamespace(), message="blocked")
        self.sent.append((chat_id, text, reply_markup))


def test_notifier_tg_delivery_and_markup():
    bot = _TgBot(fail_ids={2})
    n = ParentNotifier(tg_bot=bot)
    rows = [[cb("Счёт", "client_bill:STU-1:2026-09"), url("Оплатить", "https://x")]]
    sent = asyncio.run(n.send_many([("tg", 1), ("tg", 1), ("tg", 2), None], "hi", rows))
    assert sent == 1
    chat, text, markup = bot.sent[0]
    assert chat == 1 and text == "hi"
    btns = markup.inline_keyboard[0]
    assert btns[0].callback_data == "client_bill:STU-1:2026-09" and btns[1].url == "https://x"


def test_notifier_max_without_bot_is_not_delivered():
    n = ParentNotifier(tg_bot=_TgBot(), max_bot=None)
    assert asyncio.run(n.send(("max", 5), "hi")) is False


def test_notifier_send_to_student_counts():
    bot = _TgBot()
    n = ParentNotifier(tg_bot=bot)
    s = Student("STU-1", "Иванов", parent_tg_ids=[1], parent_max_ids=[9])
    recipients, delivered = asyncio.run(n.send_to_student(s, None, "hi"))
    assert (recipients, delivered) == (2, 1)  # max-бота нет → доставлено только в TG
