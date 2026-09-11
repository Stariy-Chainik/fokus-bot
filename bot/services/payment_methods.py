"""Канонические коды и подписи способов оплаты."""
from __future__ import annotations


CASH = "cash"
RECEIPT_BANK = "receipt_bank"
RECEIPT_UNKNOWN = "receipt_unknown"
YOOKASSA_SBP = "yookassa_sbp"
YOOKASSA_CARD = "yookassa_card"
YOOKASSA = "yookassa"
PROVIDER_ONLINE = "provider_online"
ADMIN_MANUAL = "admin_manual"


_LABELS = {
    CASH: "💵 Наличные — подтверждено администратором",
    RECEIPT_BANK: "🏦 По реквизитам — чек подтверждён",
    RECEIPT_UNKNOWN: "🧾 Чек подтверждён — способ перевода не указан",
    YOOKASSA_SBP: "📱 СБП онлайн (ЮКасса)",
    YOOKASSA_CARD: "💳 Картой онлайн (ЮКасса)",
    YOOKASSA: "💳 Онлайн (ЮКасса)",
    PROVIDER_ONLINE: "💳 Онлайн через платёжного провайдера",
    ADMIN_MANUAL: "👤 Вручную администратором",
}

_CALLBACK_CODES = {
    CASH: "c",
    RECEIPT_BANK: "b",
    RECEIPT_UNKNOWN: "r",
    ADMIN_MANUAL: "a",
}
_METHODS_BY_CALLBACK_CODE = {code: method for method, code in _CALLBACK_CODES.items()}


def label(method: str, *, confirmed_by_tg_id: int | None = None) -> str:
    """Подпись для истории. Для старых строк без метода не выдаём догадку за факт."""
    if method in _LABELS:
        return _LABELS[method]
    if confirmed_by_tg_id == 0:
        return "💳 Онлайн (ЮКасса) — точный способ не сохранён"
    return "❔ Способ не указан"


def manual_method(value: str) -> str:
    """Метод из активного родительского флоу: реквизиты с чеком или наличные."""
    return {
        "bank": RECEIPT_BANK,
        "cash": CASH,
        "receipt_unknown": RECEIPT_UNKNOWN,
    }.get(value, value if value in _LABELS else ADMIN_MANUAL)


def callback_code(method: str) -> str:
    return _CALLBACK_CODES.get(manual_method(method), "a")


def from_callback_code(code: str) -> str:
    return _METHODS_BY_CALLBACK_CODE.get(code, ADMIN_MANUAL)


def yookassa_method(payment) -> str:
    kind = getattr(getattr(payment, "payment_method", None), "type", "")
    if kind == "sbp":
        return YOOKASSA_SBP
    if kind == "bank_card":
        return YOOKASSA_CARD
    return YOOKASSA
