from .common import router as common_router
from .admin import router as admin_router
from .teacher import router as teacher_router
from .client import client_router
from .athlete import router as athlete_router

__all__ = ["common_router", "admin_router", "teacher_router", "athlete_router", "client_router"]
