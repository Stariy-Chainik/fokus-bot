from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramAPIError

from bot.models import User
from bot.repositories import StudentRepository
from bot.keyboards.client import kb_client_menu
from bot.keyboards.admin import kb_back

logger = logging.getLogger(__name__)
router = Router(name="admin_client_requests")


from bot.handlers.access import is_admin as _is_admin


@router.callback_query(F.data.startswith("admin_child_ok:"))
async def cb_admin_child_ok(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, parent_tg_id_str, student_id = callback.data.split(":", 2)
    parent_tg_id = int(parent_tg_id_str)

    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    await student_repo.add_parent_tg_id(student_id, parent_tg_id)
    logger.info("Админ одобрил: tg_id=%s → student_id=%s", parent_tg_id, student_id)

    try:
        await callback.bot.send_message(
            parent_tg_id,
            f"✅ Заявка одобрена!\n\nВы привязаны к ученику <b>{student.name}</b>.\n\nВыберите раздел:",
            reply_markup=kb_client_menu(),
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось уведомить родителя об одобрении заявки tg_id=%s: %s", parent_tg_id, exc)

    await callback.message.edit_text(
        f"✅ Одобрено\n\nУченик: {student.name}\ntg_id: {parent_tg_id}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_child_no:"))
async def cb_admin_child_no(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, parent_tg_id_str, student_id = callback.data.split(":", 2)
    parent_tg_id = int(parent_tg_id_str)

    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id
    logger.info("Админ отклонил: tg_id=%s → student_id=%s", parent_tg_id, student_id)

    try:
        await callback.bot.send_message(
            parent_tg_id,
            "❌ Администратор отклонил вашу заявку.",
            reply_markup=kb_client_menu(),
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось уведомить родителя об отклонении заявки tg_id=%s: %s", parent_tg_id, exc)

    await callback.message.edit_text(
        f"❌ Отклонено\n\nУченик: {student_name}\ntg_id: {parent_tg_id}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()
