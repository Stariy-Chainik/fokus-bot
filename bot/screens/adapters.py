from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo



def to_aiogram_markup(rows) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    out = []
    for row in rows:
        line = []
        for b in row:
            if b.kind == "app":
                line.append(InlineKeyboardButton(text=b.label, web_app=WebAppInfo(url=b.value)))
            elif b.kind == "url":
                line.append(InlineKeyboardButton(text=b.label, url=b.value))
            else:
                line.append(InlineKeyboardButton(text=b.label, callback_data=b.value))
        out.append(line)
    return InlineKeyboardMarkup(inline_keyboard=out)
