from __future__ import annotations
from ._base import _format_profit, router
from . import overview         # noqa: F401  — регистрирует хендлеры на router
from . import finance_entries  # noqa: F401
from . import daily            # noqa: F401

__all__ = ["router"]
