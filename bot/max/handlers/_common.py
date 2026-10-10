"""Общие помощники хендлеров MAX: родитель по max_uid, меню."""
from __future__ import annotations

import logging

from bot.screens.parent_menu import menu_rows, welcome_text
from bot.services.kindergarten import kindergarten_group_names
from ..render import send_screen, edit_screen, alert

logger = logging.getLogger(__name__)

# Зависимости диспетчера (build() кладёт сюда dp.workflow_data): меню нужен состав групп ребёнка,
# а show_menu зовут из многих хендлеров без репозиториев в параметрах.
DEPS: dict = {}


async def parent_students(student_repo, max_uid: int) -> list:
    return await student_repo.get_by_parent_max_id(max_uid)


async def kindergarten_children(students: list) -> list:
    """Дети садовых групп школы — им в меню «🏫 Группа в саду»."""
    group_repo, sg_repo = DEPS.get("group_repo"), DEPS.get("student_group_repo")
    if group_repo is None or sg_repo is None:
        return []
    try:
        groups = {g.group_id: g for g in await group_repo.get_all(include_archived=True)}
        return [s for s in students if await kindergarten_group_names(s.student_id, sg_repo, groups)]
    except Exception as exc:                       # меню важнее кнопки
        logger.warning("MAX: садовые группы не прочитаны: %s", exc)
        return []


async def max_menu_rows(students: list) -> list:
    return menu_rows(platform="max", kindergarten=bool(await kindergarten_children(students)))


async def show_menu(event, students: list, max_uid: int, *, new_message: bool = False) -> None:
    """Меню родителя: правка сообщения callback или новое сообщение пользователю max_uid."""
    text, rows = welcome_text(students), await max_menu_rows(students)
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
