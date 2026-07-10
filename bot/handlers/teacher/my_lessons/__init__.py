from __future__ import annotations
from ._base import router
from . import listing   # noqa: F401  — регистрирует хендлеры на router
from . import detail    # noqa: F401
from . import deletion  # noqa: F401
from . import guests    # noqa: F401

__all__ = ["router"]
