from aiogram import Router
from .record_lesson import router as record_router
from .my_lessons import router as my_lessons_router
from .my_stats import router as my_stats_router
from .partners import router as partners_router
from .submit_period import router as submit_period_router

router = Router(name="teacher")
router.include_routers(
    record_router, my_lessons_router, my_stats_router, partners_router,
    submit_period_router,
)
