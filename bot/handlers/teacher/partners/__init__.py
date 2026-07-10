from ._base import router
from . import lists            # noqa: F401  — регистрирует хендлеры на router
from . import cards            # noqa: F401
from . import partner_manage   # noqa: F401
from . import lessons_history  # noqa: F401

__all__ = ["router"]
