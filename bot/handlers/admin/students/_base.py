from __future__ import annotations
import re
import logging

from aiogram import Router, F
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

logger = logging.getLogger(__name__)
router = Router(name="admin_students")


from bot.handlers.access import is_admin as _is_admin


# ─── Общий рендер карточки ученика ────────────────────────────────────────────

async def _render_student_card(
    callback: CallbackQuery, student_id: str, back_cb: str,
    student_service: StudentService,
) -> None:
    card = await student_service.get_student_card(student_id)
    if card is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    student = card.student
    if card.teacher_names:
        teachers_text = "\n".join(f"  • {name}" for name in card.teacher_names)
    else:
        teachers_text = "  не привязан"

    if student.partner_id:
        partner_text = card.partner.name if card.partner else f"(удалён: {student.partner_id})"
    else:
        partner_text = "— (солист)"

    # Блок «Группы» — список с филиалом; тариф показываем для группы с dual-pricing.
    primary_group = card.primary_group
    if card.groups:
        group_lines: list[str] = []
        for row in card.groups:
            g = row.group
            if g:
                mode_marker = ""
                if g.billing_mode == GroupBillingMode.PER_VISIT:
                    mode_marker = f" · 💰 {g.price_full}₽/{g.duration_full}м"
                group_lines.append(f"  • <b>{g.name}</b> ({row.branch_name}){mode_marker}")
            else:
                group_lines.append(f"  • (не найдена: {row.group_id})")
        groups_block = "\n".join(group_lines)
    else:
        groups_block = "  <i>не задана</i>"

    tier_toggle = None
    tier_line = ""
    # Показ тарифа имеет смысл только для групп с dual-pricing (PER_VISIT + duration_short != duration_full).
    if primary_group is not None and primary_group.billing_mode == GroupBillingMode.PER_VISIT:
        if student.group_tier == StudentGroupTier.SHORT:
            tier_line = (
                f"\n🕐 Тариф: <b>короткий</b> — "
                f"{primary_group.duration_short} мин / {primary_group.price_short}₽"
            )
            tier_toggle = (
                student.group_tier.value,
                f"🕐 Переключить на полный ({primary_group.duration_full} мин / {primary_group.price_full}₽)",
            )
        else:
            tier_line = (
                f"\n🕐 Тариф: <b>полный</b> — "
                f"{primary_group.duration_full} мин / {primary_group.price_full}₽"
            )
            tier_toggle = (
                student.group_tier.value,
                f"🕐 Переключить на короткий ({primary_group.duration_short} мин / {primary_group.price_short}₽)",
            )

    # Блок клиента
    client_text = "\n\n👤 Клиент: не задан"
    client_rows: list = []
    if student.client_id:
        client = card.client
        if client:
            phone_hint = f" · тел: {client.phone}" if client.phone else ""
            if client.tg_id:
                client_text = f"\n\n👤 Клиент: {client.name} ✅"
            else:
                client_text = f"\n\n👤 Клиент: {client.name} (ожидает входа{phone_hint})"
            client_rows = [[("Отвязать клиента", f"student_client_unbind:{student_id}")]]
        else:
            client_rows = [[("👤 Создать клиента", f"student_client_create:{student_id}")]]
    else:
        client_rows = [[("👤 Создать клиента", f"student_client_create:{student_id}")]]

    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n"
        f"🏢 Группы:\n{groups_block}"
        f"{tier_line}\n\n"
        f"Педагоги:\n{teachers_text}\n\n"
        f"Партнёр: {partner_text}"
        f"{client_text}"
    )
    await show_card(
        callback,
        text,
        reply_markup=kb_student_card(
            student_id, has_partner=bool(student.partner_id), back_cb=back_cb,
            tier_toggle=tier_toggle,
            has_groups=bool(student.group_ids),
            client_rows=client_rows,
        ),
    )

