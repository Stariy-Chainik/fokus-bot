from ._base import router
from . import listing  # noqa: F401  — регистрирует хендлеры на router
from . import groups   # noqa: F401
from . import manage   # noqa: F401
from . import periods  # noqa: F401
from . import rates    # noqa: F401

__all__ = ["router"]
