"""Мелкие утилиты без тестов: lesson_stats, groups.hide_service_groups, notify."""
from aiogram.exceptions import TelegramBadRequest

from bot.models.enums import LessonType
from bot.utils.groups import hide_service_groups
from bot.utils.lesson_stats import format_lesson_breakdown
from bot.utils.notify import notify, notify_safely
from config.settings import settings
from tests.fakes import mk_lesson, mk_teacher, run


def test_format_lesson_breakdown():
    t = mk_teacher()
    lessons = [
        mk_lesson("L1", t, "2026-09-01", 45),
        mk_lesson("L2", t, "2026-09-02", 60),
        mk_lesson("L3", t, "2026-09-03", 45),
        mk_lesson("L4", t, "2026-09-04", 90, LessonType.GROUP, group_id="G"),
        mk_lesson("L5", t, "2026-09-05", 60, LessonType.GROUP, group_id="G"),
        mk_lesson("L6", t, "2026-09-06", 60, LessonType.GROUP, group_id="G"),
    ]
    assert format_lesson_breakdown(lessons) == (3, 3, "2×60м, 1×90м", "2×45м, 1×60м")
    assert format_lesson_breakdown([]) == (0, 0, "—", "—")


def test_hide_service_groups(monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    assert hide_service_groups(["GRP-0001", "GRP-0020", "GRP-0002"]) == {"GRP-0001", "GRP-0002"}
    monkeypatch.setattr(settings, "revenue_share_groups", "")
    assert hide_service_groups(("GRP-0020",)) == {"GRP-0020"}


class _Bot:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.fail_for:
            raise TelegramBadRequest(method=None, message="bot was blocked by the user")
        self.sent.append((chat_id, text))


def test_notify_dedups_skips_none_and_survives_delivery_errors():
    bot = _Bot(fail_for={3})
    sent = run(notify(bot, [1, None, 2, 1, 3, 2], "привет"))
    assert sent == 2
    assert bot.sent == [(1, "привет"), (2, "привет")]


def test_notify_safely_swallows_any_error(caplog):
    async def ok():
        return "sent"

    async def boom():
        raise RuntimeError("network down")

    assert run(notify_safely(ok(), "не должно логироваться: %s")) is True
    with caplog.at_level("ERROR"):
        assert run(notify_safely(boom(), "Не удалось уведомить педагога: %s")) is False
    assert "Не удалось уведомить педагога: network down" in caplog.text
