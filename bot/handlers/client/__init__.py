from aiogram import Router
from .menu import router as menu_router
from .auth import router as auth_router
from .schedule import router as schedule_router
from .billing import router as billing_router
from .payments import router as payments_router

router = Router(name="client")
router.include_routers(
    menu_router, auth_router, schedule_router, billing_router, payments_router,
)
