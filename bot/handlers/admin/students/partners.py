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


# ─── Управление партнёром ученика ────────────────────────────────────────────

@router.callback_query(F.data.startswith("partner_assign:"))
async def cb_partner_assign_start(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    student_repo: StudentRepository,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    # Кандидаты — ученики, у которых есть хотя бы одна общая группа с текущим.
    candidates = await student_service.partner_candidates(student)
    if candidates is None:
        await callback.message.edit_text(
            "У ученика не задана группа — сначала назначьте группу.",
            reply_markup=kb_back(f"student_card:{student_id}"),
        )
        await callback.answer()
        return

    if not candidates:
        await callback.message.edit_text(
            "В группе нет других учеников для пары.",
            reply_markup=kb_back(f"student_card:{student_id}"),
        )
        await callback.answer()
        return

    await state.set_state(PartnerAssignStates.choosing_partner)
    await state.update_data(student_id=student_id)
    await callback.message.edit_text(
        f"<b>Выберите партнёра для «{student.name}».</b>\n"
        f"⚠️ — у ученика уже есть партнёр, старая связь будет разорвана.",
        reply_markup=kb_partner_candidates(candidates, student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("partner_pick:"), PartnerAssignStates.choosing_partner)
async def cb_partner_pick(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    partner_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    student_id = data.get("student_id", "")
    a = await student_repo.get_by_id(student_id)
    b = await student_repo.get_by_id(partner_id)
    if not a or not b:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    lines = [f"<b>Назначить партнёрами:</b>", f"• {a.name}", f"• {b.name}"]
    # Предупреждения о разрыве старых связей.
    old_links = []
    if a.partner_id and a.partner_id != partner_id:
        prev = await student_repo.get_by_id(a.partner_id)
        old_links.append(prev.name if prev else a.partner_id)
    if b.partner_id and b.partner_id != student_id:
        prev = await student_repo.get_by_id(b.partner_id)
        old_links.append(prev.name if prev else b.partner_id)
    if old_links:
        lines.append("")
        lines.append("⚠️ Старые связи будут разорваны: " + ", ".join(old_links))
    lines.append("")
    lines.append("Продолжить?")

    admin_pair_group_id = data.get("admin_pair_group_id")
    cancel_cb = (
        f"sp_grp:pairs:{admin_pair_group_id}"
        if admin_pair_group_id else f"student_card:{student_id}"
    )
    await state.update_data(partner_id=partner_id)
    await state.set_state(PartnerAssignStates.confirming)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_confirm("confirm_partner", cancel_cb),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_partner", PartnerAssignStates.confirming)
async def cb_partner_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    student_id = data.get("student_id", "")
    partner_id = data.get("partner_id", "")
    admin_pair_group_id = data.get("admin_pair_group_id")
    back_cb = (
        f"sp_grp:pairs:{admin_pair_group_id}"
        if admin_pair_group_id else f"student_card:{student_id}"
    )
    try:
        await student_repo.set_partner(student_id, partner_id)
        await callback.message.edit_text(
            "Партнёры назначены.", reply_markup=kb_back(back_cb),
        )
    except ValueError as exc:
        await callback.message.edit_text(
            f"Ошибка: {exc}", reply_markup=kb_back(back_cb),
        )
    except Exception as exc:
        logger.error("Ошибка назначения партнёра: %s", exc)
        await callback.message.edit_text(
            "Не удалось назначить партнёра. Попробуйте позже.",
            reply_markup=kb_back(back_cb),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("partner_clear:"))
async def cb_partner_clear_confirm(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student or not student.partner_id:
        await callback.answer("У ученика нет партнёра", show_alert=True)
        return
    partner = await student_repo.get_by_id(student.partner_id)
    partner_name = partner.name if partner else student.partner_id
    await callback.message.edit_text(
        f"<b>Убрать пару: «{student.name}» ↔ «{partner_name}»?</b>",
        reply_markup=kb_confirm(
            f"confirm_partner_clear:{student_id}", f"student_card:{student_id}",
            confirm_text="❌ Убрать",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_partner_clear:"))
async def cb_partner_clear_do(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    try:
        await student_repo.clear_partner(student_id)
        await callback.message.edit_text(
            "Партнёр снят.", reply_markup=kb_back(f"student_card:{student_id}")
        )
    except Exception as exc:
        logger.error("Ошибка снятия партнёра: %s", exc)
        await callback.message.edit_text(
            "Не удалось снять партнёра.",
            reply_markup=kb_back(f"student_card:{student_id}"),
        )
    await callback.answer()


