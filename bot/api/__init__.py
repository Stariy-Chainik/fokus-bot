from .admin import register_admin_api
from .miniapp import register_miniapp_api
from .static import register_miniapp_static

__all__ = ["register_admin_api", "register_miniapp_api", "register_miniapp_static"]
