from .group_link import router as group_link_router
from .receipt_email import router as receipt_email_router
from .start import router as start_router
from .my_bills import router as bills_router
from .my_lessons import router as lessons_router
from .payments import router as payments_router
from .diary import router as diary_router

from aiogram import Router

client_router = Router(name="client")
# group_link — раньше start: его CommandStart(deep_link=True) и contact-состояние
# должны срабатывать до catch-all F.text регистрации по фамилии.
client_router.include_routers(
    group_link_router, receipt_email_router, start_router, bills_router,
    lessons_router, payments_router, diary_router,
)

__all__ = ["client_router"]
