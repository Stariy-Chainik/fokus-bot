from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from datetime import date

from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository, StudentGroupRepository,
)
from bot.services import PaymentService
from bot.states import (
    AddBranchStates, EditBranchNameStates,
    AddGroupStates, EditGroupNameStates,
    GroupBillingStates, GroupAddStudentStates,
)
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period
from bot.utils.locks import InProgressGuard
from bot.handlers.common import show_card
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Массовая рассылка счетов группе ─────────────────────────────────────────

_group_send_in_progress = InProgressGuard()


@router.callback_query(F.data.startswith("group_send_bills:"))
async def cb_group_send_bills(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    payment_service: PaymentService,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    period_month = date.today().strftime("%Y-%m")
    lock_key = f"{group_id}:{period_month}"
    if lock_key in _group_send_in_progress:
        await callback.answer("Рассылка уже выполняется", show_alert=True)
        return
    _group_send_in_progress.add(lock_key)

    try:
        member_ids = set(await student_group_repo.get_students_for_group(group_id))
        students = [s for s in await student_repo.get_all() if s.student_id in member_ids]
        if not students:
            await callback.answer("В группе нет учеников", show_alert=True)
            return

        per_student_bills: dict[str, dict] = {}
        any_bills = False
        for s in students:
            bills = await payment_service.compute_bills_for_student_period(
                s.student_id, period_month,
            )
            per_student_bills[s.student_id] = bills
            if bills:
                any_bills = True

        if not any_bills:
            await callback.answer(
                f"За {display_period(period_month)} нет занятий к оплате у учеников группы.",
                show_alert=True,
            )
            return

        total_invoices = 0
        for s in students:
            if not per_student_bills[s.student_id]:
                continue
            invoices = await payment_service.get_or_create_invoices_for_student_period(
                s, period_month,
            )
            total_invoices += len(invoices)
        await callback.answer(
            f"Счёта группы «{group.name}» за {display_period(period_month)} разосланы родителям (заглушка). "
            f"Учеников: {len(students)}, счетов: {total_invoices}.",
            show_alert=True,
        )
    finally:
        _group_send_in_progress.discard(lock_key)

