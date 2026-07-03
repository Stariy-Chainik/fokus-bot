from __future__ import annotations
import re
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, Student, StudentRequest, GroupBillingMode, StudentGroupTier
from bot.repositories import (
    StudentRepository,
    GroupRepository, BranchRepository, StudentGroupRepository,
    StudentRequestRepository, ClientRepository,
)
from bot.services import (
    StudentService, TierToggleError,
    StudentRequestService, LinkExistingOutcome,
)
from bot.models.enums import RequestStatus
from bot.states import AddStudentStates, StudentListStates, PartnerAssignStates, ClientCreateStates
from bot.handlers.common import show_card
from bot.keyboards.admin import (
    kb_students_menu,
    kb_student_paged, kb_student_card, kb_partner_candidates,
    kb_confirm, kb_back, _STUDENT_PAGE_SIZE,
)
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)
from ._base import _render_student_card


# ─── Управление клиентом ученика ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("student_client_create:"))
async def cb_student_client_create(
    callback: CallbackQuery, user: User | None,
    state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    if student.client_id:
        await callback.answer("У ученика уже есть клиент", show_alert=True)
        return
    await state.set_state(ClientCreateStates.entering_phone)
    await state.update_data(student_id=student_id)
    await callback.message.edit_text(
        f"<b>Создать клиента для «{student.name}»</b>\n\nВведите номер телефона клиента:",
        reply_markup=kb_back(f"student_card:{student_id}"),
    )
    await callback.answer()


@router.message(ClientCreateStates.entering_phone)
async def handle_client_phone(
    message: Message, state: FSMContext,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
) -> None:
    phone = (message.text or "").strip()
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 10:
        await message.answer("Неверный формат. Введите номер телефона (например: +79001234567):")
        return
    data = await state.get_data()
    student_id = data.get("student_id", "")
    await state.clear()
    student = await student_repo.get_by_id(student_id)
    if not student:
        await message.answer("Ученик не найден.", reply_markup=kb_back("admin:students"))
        return
    try:
        client = await client_repo.create(student.name, message.from_user.id, phone)
        await student_repo.set_client_id(student_id, client.client_id)
    except Exception as exc:
        logger.error("Ошибка создания клиента: %s", exc)
        await message.answer("Ошибка при создании клиента.", reply_markup=kb_back(f"student_card:{student_id}"))
        return
    await message.answer(
        f"✅ Клиент создан\n\n"
        f"Имя: {client.name}\nТелефон: {phone}\n\n"
        f"Клиент сможет войти через кнопку «Поделиться номером» при первом запуске бота.",
        reply_markup=kb_back(f"student_card:{student_id}"),
    )


@router.callback_query(F.data.startswith("student_client_unbind:"))
async def cb_student_client_unbind(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student or not student.client_id:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    client = await client_repo.get_by_id(student.client_id)
    client_name = client.name if client else "—"
    await show_card(
        callback,
        f"Отвязать клиента «{client_name}» от ученика «{student.name}»?\n\n"
        f"Клиент больше не сможет войти через Telegram-бот.",
        reply_markup=kb_confirm(
            f"confirm_student_client_unbind:{student_id}",
            f"student_card:{student_id}",
            confirm_text="Отвязать",
        ),
    )


@router.callback_query(F.data.startswith("confirm_student_client_unbind:"))
async def cb_student_client_unbind_confirm(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, client_repo: ClientRepository,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student or not student.client_id:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    await client_repo.clear_tg_id(student.client_id)
    await _render_student_card(callback, student_id, "students:list", student_service)
