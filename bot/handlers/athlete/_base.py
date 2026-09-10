"""Общее для кабинета спортсмена: роутер, гард, периоды."""
from __future__ import annotations
import logging
from datetime import date
from typing import Optional

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from bot.models import Student
from bot.services import DiaryService
from bot.utils.dates import last_periods

logger = logging.getLogger(__name__)
router = Router(name="athlete")


async def athlete_of(event: CallbackQuery | Message, diary_service: DiaryService) -> Optional[Student]:
    """Ученик-спортсмен по tg_id отправителя; None + алерт, если роли нет."""
    student = await diary_service.athlete_by_tg(event.from_user.id)
    if student is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Кабинет спортсмена не привязан. Отправьте /start", show_alert=True)
        else:
            await event.answer("Кабинет спортсмена не привязан. Отправьте /start")
    return student


def periods() -> tuple[str, str]:
    """(текущий месяц, прошлый месяц) в формате YYYY-MM."""
    this, prev = last_periods(2)
    return this, prev


def today_iso() -> str:
    return date.today().isoformat()
