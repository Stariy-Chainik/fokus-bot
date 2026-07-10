from ._base import router
from . import viewing  # noqa: F401  — регистрирует хендлеры на router
from . import payment  # noqa: F401

__all__ = ["router"]
