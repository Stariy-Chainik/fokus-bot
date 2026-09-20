from .admin import register_admin_api
from .miniapp import register_miniapp_api
from .parent import register_parent_api
from .static import register_miniapp_static
from .teacher import register_teacher_api

__all__ = [
    "register_admin_api", "register_miniapp_api", "register_miniapp_static",
    "register_parent_api", "register_teacher_api",
]
