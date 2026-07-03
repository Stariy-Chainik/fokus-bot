from ._base import router
from . import entry     # noqa: F401  — регистрирует хендлеры на router
from . import schedule  # noqa: F401
from . import group     # noqa: F401
from . import soloist   # noqa: F401
from . import pair      # noqa: F401
from . import shared    # noqa: F401

__all__ = ["router"]
