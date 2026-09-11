from types import SimpleNamespace

from bot.handlers.admin.payment_history import _method


def _row(method="", confirmed_by=7):
    return SimpleNamespace(payment_method=method, confirmed_by_tg_id=confirmed_by)


def test_history_shows_exact_new_method():
    assert "Наличные" in _method(_row("cash"))
    assert "По реквизитам" in _method(_row("receipt_bank"))
    assert "СБП онлайн" in _method(_row("yookassa_sbp", 0))


def test_history_does_not_invent_legacy_manual_method():
    assert _method(_row()) == "❔ Способ не указан"
