from ._base import router
from . import view     # noqa: F401  — регистрирует хендлеры на router
from . import send     # noqa: F401
from . import confirm  # noqa: F401

__all__ = ["router"]
