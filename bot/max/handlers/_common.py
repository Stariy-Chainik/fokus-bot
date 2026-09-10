"""Общие помощники хендлеров MAX: родитель по max_uid, меню."""
from __future__ import annotations

from bot.screens.parent_menu import menu_rows, welcome_text
from ..render import send_screen, edit_screen, alert


async def parent_students(student_repo, max_uid: int) -> list:
    return await student_repo.get_by_parent_max_id(max_uid)


async def show_menu(event, students: list, max_uid: int, *, new_message: bool = False) -> None:
    """Меню родителя: правка сообщения callback или новое сообщение пользователю max_uid."""
    text, rows = welcome_text(students), menu_rows(platform="max")
    if new_message or not hasattr(event, "callback"):
        await send_screen(event.bot, max_uid, text, rows)
    else:
        await edit_screen(event, text, rows)


async def require_parent(event, student_repo, max_uid: int) -> list | None:
    students = await parent_students(student_repo, max_uid)
    if not students:
        await alert(event, "Кабинет не привязан. Отправьте /start")
        return None
    return students
