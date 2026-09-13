"""Изоляция тестов от локального .env: настройки-списки (педагоги/группы)
сбрасываются в пустые, чтобы прод-конфиг не менял ожидания тестов."""
import pytest

from config.settings import settings

_RESET = {
    "direct_pay_teacher_ids": "",
    "billing_teacher_ids": "",
    "revenue_share_groups": "",
    "salary_duration_groups": "",
    "shift_groups": "",
    "hall_rent_per_lesson": "",
    "hall_rent_since_period": "",
    "parent_receipt_email": True,
    "athlete_group_ids": "GRP-0001",
}


@pytest.fixture(autouse=True)
def _neutral_settings(monkeypatch):
    for name, value in _RESET.items():
        monkeypatch.setattr(settings, name, value)
    from bot.services import rate_history
    rate_history.load([])
    yield
    rate_history.load([])
