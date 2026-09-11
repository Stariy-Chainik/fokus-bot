from types import SimpleNamespace

from bot.services.payment_methods import (
    ADMIN_MANUAL, CASH, RECEIPT_BANK, RECEIPT_UNKNOWN,
    callback_code, from_callback_code, label, manual_method, yookassa_method,
)


def test_manual_methods_round_trip_through_short_callback_code():
    expected = {
        "cash": CASH,
        "bank": RECEIPT_BANK,
        "receipt_unknown": RECEIPT_UNKNOWN,
    }
    for incoming, stored in expected.items():
        assert manual_method(incoming) == stored
        assert from_callback_code(callback_code(incoming)) == stored
    assert from_callback_code("") == ADMIN_MANUAL


def test_labels_are_exact_and_legacy_is_honest():
    assert "Наличные" in label(CASH)
    assert "По реквизитам" in label(RECEIPT_BANK)
    assert "Способ не указан" in label("", confirmed_by_tg_id=123)
    assert "точный способ не сохранён" in label("", confirmed_by_tg_id=0)


def test_yookassa_method_comes_from_verified_api_object():
    assert yookassa_method(SimpleNamespace(payment_method=SimpleNamespace(type="sbp"))) == "yookassa_sbp"
    assert yookassa_method(SimpleNamespace(payment_method=SimpleNamespace(type="bank_card"))) == "yookassa_card"
    assert yookassa_method(SimpleNamespace()) == "yookassa"
