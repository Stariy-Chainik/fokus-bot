from ._base import router
from . import crud_branches  # noqa: F401  — регистрирует хендлеры на router
from . import groups         # noqa: F401
from . import bulk           # noqa: F401
from . import billing        # noqa: F401
from . import members        # noqa: F401

__all__ = ["router"]
