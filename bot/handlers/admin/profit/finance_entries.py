from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.models import User
from bot.repositories import FinanceEntryRepository
from bot.services import ProfitService
from bot.states import FinanceEntryStates
from bot.utils.dates import display_period

from ._base import _is_admin, _period_view, logger, router

_FIN_KIND_LABELS = {"income": "доход", "expense": "расход"}


@router.callback_query(F.data.startswith("fin:add:"))
async def cb_fin_add(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, kind, period = callback.data.split(":", 3)
    await state.set_state(FinanceEntryStates.entering_title)
    await state.update_data(fin_kind=kind, fin_period=period)
    example = "Турнир «Осенний кубок»" if kind == "income" else "Аренда зала"
    await callback.message.edit_text(
        f"<b>➕ {_FIN_KIND_LABELS[kind].capitalize()} за {display_period(period)}</b>\n\n"
        f"Введите название (например, «{example}»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="« Отмена",
                callback_data=f"profit_period:{period}",
            ),
        ]]),
    )
    await callback.answer()


@router.message(FinanceEntryStates.entering_title)
async def fin_title_entered(message: Message, state: FSMContext) -> None:
    title = " ".join((message.text or "").split())
    if not title:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    await state.update_data(fin_title=title[:100])
    await state.set_state(FinanceEntryStates.entering_amount)
    await message.answer(
        f"«{title[:100]}» — введите сумму в рублях (целое число):"
    )


@router.message(FinanceEntryStates.entering_amount)
async def fin_amount_entered(
    message: Message,
    state: FSMContext,
    profit_service: ProfitService,
    finance_entry_repo: FinanceEntryRepository,
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "Нужно целое число рублей больше 0. Введите ещё раз:"
        )
        return
    data = await state.get_data()
    await state.clear()
    kind = data["fin_kind"]
    period = data["fin_period"]
    title = data["fin_title"]
    entry = await finance_entry_repo.add(period, kind, title, int(raw))
    logger.info(
        "Финансовая запись %s: %s «%s» %d ₽ за %s",
        entry.entry_id,
        kind,
        title,
        entry.amount,
        period,
    )
    text, keyboard = await _period_view(period, profit_service)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("fin:list:"))
async def cb_fin_list(
    callback: CallbackQuery,
    user: User | None,
    finance_entry_repo: FinanceEntryRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 2)[2]
    entries = await finance_entry_repo.get_by_period(period)
    if not entries:
        await callback.answer("Записей нет", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=(
                f"✖ {'🏆' if entry.kind == 'income' else '📉'} "
                f"{entry.title} — {entry.amount} ₽"
            ),
            callback_data=f"fin:del:{entry.entry_id}:{period}",
        )]
        for entry in entries
    ]
    rows.append([InlineKeyboardButton(
        text="« Назад",
        callback_data=f"profit_period:{period}",
    )])
    await callback.message.edit_text(
        "<b>🗑 Удалить запись</b>\n\nНажмите на строку, чтобы удалить её:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fin:del:"))
async def cb_fin_delete(
    callback: CallbackQuery,
    user: User | None,
    profit_service: ProfitService,
    finance_entry_repo: FinanceEntryRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, entry_id, period = callback.data.split(":", 3)
    deleted = await finance_entry_repo.delete(entry_id)
    text, keyboard = await _period_view(period, profit_service)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Удалено" if deleted else "Уже удалено")
