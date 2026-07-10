from ._base import router
from . import roster      # noqa: F401  — регистрирует хендлеры на router
from . import members     # noqa: F401
from . import attendance  # noqa: F401

__all__ = ["router"]
