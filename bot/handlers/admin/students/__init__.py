from ._base import router
from . import overview   # noqa: F401  — регистрирует хендлеры на router
from . import listing    # noqa: F401
from . import add        # noqa: F401
from . import delete     # noqa: F401
from . import partners   # noqa: F401
from . import groups     # noqa: F401
from . import requests   # noqa: F401
from . import client     # noqa: F401

__all__ = ["router"]
