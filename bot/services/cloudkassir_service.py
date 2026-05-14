from __future__ import annotations
import logging
import re

import aiohttp

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cloudpayments.ru"
_MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+7{digits}"
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return f"+7{digits[1:]}"
    return f"+{digits}"


class CloudKassirService:
    def __init__(self, public_id: str, api_secret: str) -> None:
        self._public_id = public_id
        self._api_secret = api_secret

    async def send_income_receipt(
        self,
        phone: str,
        student_name: str,
        period_month: str,
        total_amount: int,
    ) -> bool:
        year, month = period_month.split("-")
        label = f"Занятия — {_MONTHS_RU[int(month)]} {year}, {student_name}"[:128]

        payload = {
            "Type": "Income",
            "CustomerReceipt": {
                "Phone": _normalize_phone(phone),
                "Items": [{
                    "Label": label,
                    "Price": total_amount,
                    "Quantity": 1.00,
                    "Amount": total_amount,
                    "Vat": None,
                    "Method": 4,
                    "Object": 4,
                }],
                "Amounts": {
                    "Electronic": total_amount,
                    "Cash": 0,
                    "AdvancePayment": 0,
                    "Credit": 0,
                    "Provision": 0,
                },
                "TaxationSystem": 5,
            },
        }

        auth = aiohttp.BasicAuth(self._public_id, self._api_secret)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_BASE_URL}/kkt/receipt",
                    json=payload,
                    auth=auth,
                ) as resp:
                    body = await resp.json()
                    if body.get("Success"):
                        logger.info(
                            "Фискальный чек выбит: id=%s student=%s period=%s",
                            body.get("Id"), student_name, period_month,
                        )
                        return True
                    logger.error(
                        "CloudKassir ошибка %s: %s (student=%s period=%s)",
                        body.get("Code"), body.get("Message"), student_name, period_month,
                    )
                    return False
        except Exception as exc:
            logger.error("CloudKassir HTTP ошибка: %s", exc)
            return False
