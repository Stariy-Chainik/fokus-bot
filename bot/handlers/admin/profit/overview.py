from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.models import User
from bot.services import ProfitService

from ._base import _is_admin, _period_buttons, _period_view, router


@router.callback_query(F.data == "profit:view")
async def cb_profit_view(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите период:",
        reply_markup=_period_buttons(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profit_period:"))
async def cb_profit_period(
    callback: CallbackQuery,
    user: User | None,
    profit_service: ProfitService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    text, keyboard = await _period_view(period, profit_service)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()
