from __future__ import annotations
import logging

from aiogram import Router

from bot.utils.locks import InProgressGuard

logger = logging.getLogger(__name__)
router = Router(name="admin_bills")

_confirming_in_progress = InProgressGuard()
_sending_in_progress = InProgressGuard()
_group_sending = InProgressGuard()




