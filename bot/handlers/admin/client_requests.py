from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from config.settings import settings
from bot.models import User
from bot.repositories import StudentRepository
from bot.screens.parent_menu import menu_rows
from bot.services.parent_notifier import resolve_notifier, parse_addr
from bot.keyboards.admin import kb_back
from bot.handlers.filters import AdminOnly

logger = logging.getLogger(__name__)
router = Router(name="admin_client_requests")



@router.callback_query(F.data.startswith("admin_child_ok:"), AdminOnly())
async def cb_admin_child_ok(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
) -> None:

    _, parent_raw, student_id = callback.data.split(":", 2)
    parent_addr = parse_addr(parent_raw)

    student = await student_repo.get_by_id(student_id)
    if not student or parent_addr is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    await student_repo.add_parent(student_id, parent_addr)
    logger.info("Админ одобрил: %s → student_id=%s", parent_raw, student_id)

    await resolve_notifier(callback.bot).send(
        parent_addr,
        f"✅ Заявка одобрена!\n\nВы привязаны к ученику <b>{student.name}</b>.\n\nВыберите раздел:",
        rows=menu_rows(platform=parent_addr[0], receipt_email=settings.parent_receipt_email),
    )

    await callback.message.edit_text(
        f"✅ Одобрено\n\nУченик: {student.name}\nРодитель: {parent_raw}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_child_no:"), AdminOnly())
async def cb_admin_child_no(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
) -> None:

    _, parent_raw, student_id = callback.data.split(":", 2)
    parent_addr = parse_addr(parent_raw)

    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id
    logger.info("Админ отклонил: %s → student_id=%s", parent_raw, student_id)

    if parent_addr:
        await resolve_notifier(callback.bot).send(
            parent_addr, "❌ Администратор отклонил вашу заявку.",
            rows=menu_rows(platform=parent_addr[0], receipt_email=settings.parent_receipt_email),
        )

    await callback.message.edit_text(
        f"❌ Отклонено\n\nУченик: {student_name}\nРодитель: {parent_raw}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()
