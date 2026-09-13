from __future__ import annotations

from typing import Callable

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.paging import Page


def kb_mode_select() -> InlineKeyboardMarkup:
    """Выбор режима для пользователя, у которого есть и is_admin, и teacher_id."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👔 Администратор", callback_data="mode:admin")],
        [InlineKeyboardButton(text="🎓 Педагог", callback_data="mode:teacher")],
    ])


def kb_mode_select_family() -> InlineKeyboardMarkup:
    """Один Telegram и спортсмен, и родитель другого ребёнка — выбор кабинета."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Кабинет спортсмена", callback_data="mode:athlete")],
        [InlineKeyboardButton(text="👨‍👩‍👧 Кабинет родителя", callback_data="mode:client")],
    ])


def kb_welcome_choice() -> InlineKeyboardMarkup:
    """Первый /start неизвестного пользователя: родитель или спортсмен."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍👩‍👧 Я родитель", callback_data="athreg:parent")],
        [InlineKeyboardButton(text="🏃 Я спортсмен", callback_data="athreg:athlete")],
    ])


def nav_row(
    pg: Page, callback_for: Callable[[int], str], *,
    prev_text: str = "← Пред.", next_text: str = "След. →", counter: bool = False,
) -> list[InlineKeyboardButton]:
    """Кнопки «назад / вперёд» по странице; пустой список, если листать некуда.

    counter=True вставляет между ними «N/M» (noop) — стиль экрана «Должники».
    """
    row: list[InlineKeyboardButton] = []
    if pg.has_prev:
        row.append(InlineKeyboardButton(text=prev_text, callback_data=callback_for(pg.page - 1)))
    if counter:
        row.append(InlineKeyboardButton(text=f"{pg.page + 1}/{pg.pages}", callback_data="noop"))
    if pg.has_next:
        row.append(InlineKeyboardButton(text=next_text, callback_data=callback_for(pg.page + 1)))
    return row
