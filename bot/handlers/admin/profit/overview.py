from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.handlers.filters import AdminOnly
from bot.models import User
from bot.services import ProfitService

from ._base import _period_buttons, _period_view, router


@router.callback_query(F.data == "profit:view", AdminOnly())
async def cb_profit_view(callback: CallbackQuery, user: User) -> None:
    await callback.message.edit_text(
        "Выберите период:",
        reply_markup=_period_buttons(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_period:"), AdminOnly())
async def cb_profit_period(
    callback: CallbackQuery,
    user: User,
    profit_service: ProfitService,
) -> None:
    period = callback.data.split(":", 1)[1]
    text, keyboard = await _period_view(period, profit_service)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()
