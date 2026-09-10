"""Право педагога на счета (BILLING_TEACHER_IDS): парсинг настройки и предикат."""
import pytest

from bot.handlers.access import can_teacher_bill
from bot.models import User
from config.settings import settings


def _user(tg_id=1, is_admin=False, teacher_id=None):
    return User(user_id="USR-0001", tg_id=tg_id, is_admin=is_admin, teacher_id=teacher_id)


@pytest.fixture()
def billing_ids(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(settings, "billing_teacher_ids", value)
    return _set


def test_id_set_parses_comma_pipe_and_spaces(billing_ids):
    billing_ids("TCH-0009, TCH-0002|TCH-0005")
    assert settings.billing_teacher_id_set == {"TCH-0009", "TCH-0002", "TCH-0005"}


def test_id_set_empty(billing_ids):
    billing_ids("")
    assert settings.billing_teacher_id_set == set()


def test_can_bill_only_listed_teacher(billing_ids):
    billing_ids("TCH-0009")
    assert can_teacher_bill(_user(teacher_id="TCH-0009"))
    assert not can_teacher_bill(_user(teacher_id="TCH-0005"))
    assert not can_teacher_bill(_user(is_admin=True))  # админ без teacher_id — не этот предикат
    assert not can_teacher_bill(None)


def test_can_bill_nobody_when_unset(billing_ids):
    billing_ids("")
    assert not can_teacher_bill(_user(teacher_id="TCH-0009"))
