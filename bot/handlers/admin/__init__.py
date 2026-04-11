from aiogram import Router
from .teachers import router as teachers_router
from .students import router as students_router
from .salaries import router as salaries_router
from .bills import router as bills_router
from .diagnostics import router as diagnostics_router
from .edit_lesson import router as edit_lesson_router

router = Router(name="admin")
router.include_routers(
    teachers_router,
    students_router,
    salaries_router,
    bills_router,
    diagnostics_router,
    edit_lesson_router,
)
