"""Экраны родителя без привязки к мессенджеру: (текст, ряды кнопок).

Кнопка — `Btn(label, kind, value)`, где kind = "cb" (callback payload) или "url".
Адаптеры: `bot/screens/adapters.py::to_aiogram_markup` (Telegram),
`bot/max/render.py::to_max_markup` (MAX).
"""
from .types import Btn, Rows, Screen, app, cb, url

__all__ = ["Btn", "Rows", "Screen", "app", "cb", "url"]
