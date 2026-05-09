from .start import router as start_router
from .my_bills import router as bills_router
from .my_lessons import router as lessons_router
from .payments import router as payments_router

from aiogram import Router

client_router = Router(name="client")
client_router.include_routers(start_router, bills_router, lessons_router, payments_router)

__all__ = ["client_router"]
