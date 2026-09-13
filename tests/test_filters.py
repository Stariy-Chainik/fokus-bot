"""Фильтры доступа: пропускают свою роль, чужой показывают alert «Нет доступа»."""
import pytest

from bot.handlers.filters import AdminOnly, BillingTeacherOnly, TeacherOnly, TeacherOrAdmin
from config.settings import settings
from tests.fakes import FakeCallbackQuery, mk_user, run

ADMIN = mk_user(1, is_admin=True)
TEACHER = mk_user(7, teacher_id="TCH-0001")
BOTH = mk_user(9, is_admin=True, teacher_id="TCH-0009")
BILLING = mk_user(8, teacher_id="TCH-0009")


@pytest.mark.parametrize("flt, allowed, denied", [
    (AdminOnly(), [ADMIN, BOTH], [None, TEACHER, BILLING]),
    (TeacherOnly(), [TEACHER, BOTH, BILLING], [None, ADMIN]),
    (TeacherOrAdmin(), [ADMIN, TEACHER, BOTH, BILLING], [None]),
    (BillingTeacherOnly(), [BILLING, BOTH], [None, ADMIN, TEACHER]),
], ids=["admin", "teacher", "teacher_or_admin", "billing_teacher"])
def test_role_filters(monkeypatch, flt, allowed, denied):
    monkeypatch.setattr(settings, "billing_teacher_ids", "TCH-0009")
    for user in allowed:
        cb = FakeCallbackQuery("x")
        assert run(flt(cb, user=user)) is True and cb.alerts == []
    for user in denied:
        cb = FakeCallbackQuery("x")
        assert run(flt(cb, user=user)) is False
        assert cb.alerts == [("Нет доступа", True)]


def test_filter_ignores_extra_handler_data():
    cb = FakeCallbackQuery("x")
    assert run(AdminOnly()(cb, user=ADMIN, state=object(), student_repo=object())) is True
