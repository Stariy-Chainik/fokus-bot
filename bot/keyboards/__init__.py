from .common import kb_mode_select
from .admin import (
    kb_admin_menu,
    kb_teachers_menu,
    kb_students_menu,
    kb_salaries_menu,
)
from .teacher import kb_teacher_menu
from .calendar import kb_calendar

__all__ = [
    "kb_mode_select",
    "kb_admin_menu", "kb_teachers_menu", "kb_students_menu",
    "kb_salaries_menu",
    "kb_teacher_menu",
    "kb_calendar",
]
