"""Кабинет родителя в мессенджере MAX — второй фронт на тех же репозиториях и сервисах.

Библиотека `maxapi` (Python ≥ 3.10) импортируется лениво: если она не установлена
или MAX_BOT_TOKEN пуст, Telegram-бот работает как прежде.
"""
from __future__ import annotations

try:
    import maxapi  # noqa: F401
    available = True
except ImportError:  # pragma: no cover
    available = False


def build_max(token: str, deps: dict, tg_bot):
    """(max_bot, max_dp) для запуска polling; см. bot/max/app.py."""
    from .app import build
    return build(token, deps, tg_bot)
