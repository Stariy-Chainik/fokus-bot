from .common import router as common_router
from .admin import router as admin_router
from .teacher import router as teacher_router
from .client import router as client_router

__all__ = ["common_router", "admin_router", "teacher_router", "client_router"]
