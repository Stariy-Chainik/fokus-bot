from __future__ import annotations
import logging

from aiogram import Router


from bot.utils.dates import month_name_ru

logger = logging.getLogger(__name__)
router = Router(name="client_bills")

def _period_label(period_month: str) -> str:
    year, month = period_month.split("-")
    return f"{month_name_ru(int(month))} {year}"


