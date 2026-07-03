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


@router.callback_query(F.data == "admin:students")
async def cb_students_menu(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("<b>Управление учениками:</b>", reply_markup=kb_students_menu())
    await callback.answer()


# ─── Пары и солисты: филиал → группа → список ───────────────────────────────

@router.callback_query(F.data.in_({"students:pairs", "students:soloists"}))
async def cb_pairs_soloists_branches(
    callback: CallbackQuery, user: User | None, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mode = "pairs" if "pairs" in callback.data else "soloists"
    branches = await branch_repo.get_all()
    if not branches:
        await callback.message.edit_text("Филиалов нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=b.name, callback_data=f"sp_brn:{mode}:{b.branch_id}")]
        for b in branches
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    label = "Пары" if mode == "pairs" else "Солисты"
    await callback.message.edit_text(
        f"<b>{label} — выберите филиал:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sp_brn:"))
async def cb_pairs_soloists_groups(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, mode, branch_id = callback.data.split(":")
    groups = sorted([g for g in await group_repo.get_all() if g.branch_id == branch_id], key=lambda g: (g.sort_order, g.name))
    if not groups:
        await callback.message.edit_text(
            "В этом филиале нет групп.",
            reply_markup=kb_back(f"students:{mode}"),
        )
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=g.name, callback_data=f"sp_grp:{mode}:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"students:{mode}")])
    label = "Пары" if mode == "pairs" else "Солисты"
    await callback.message.edit_text(
        f"<b>{label} — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sp_grp:"))
async def cb_pairs_soloists_list(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, mode, group_id = callback.data.split(":")
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id

    if mode == "pairs":
        pairs = await student_service.pairs_in_group(group_id)
        back_cb = f"sp_brn:{mode}:{group.branch_id}" if group else "admin:students"
        if not pairs:
            await callback.message.edit_text(
                f"В группе «{group_name}» пар нет.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Создать пару", callback_data=f"admin_create_pair:{group_id}")],
                    [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
                ]),
            )
            await callback.answer()
            return

        buttons = []
        for a, b in pairs:
            buttons.append([InlineKeyboardButton(
                text=f"{a.name} ↔ {b.name}",
                callback_data=f"student_card_sp:{mode}:{group_id}:{a.student_id}",
            )])
        buttons.append([InlineKeyboardButton(text="➕ Создать пару", callback_data=f"admin_create_pair:{group_id}")])
        buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
        await callback.message.edit_text(
            f"<b>Пары — {group_name} ({len(pairs)}):</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    else:
        soloists = await student_service.soloists_in_group(group_id)
        if not soloists:
            await callback.message.edit_text(
                f"В группе «{group_name}» солистов нет.",
                reply_markup=kb_back(f"sp_brn:{mode}:{group.branch_id}" if group else "admin:students"),
            )
            await callback.answer()
            return

        buttons = [
            [InlineKeyboardButton(
                text=s.name,
                callback_data=f"student_card_sp:{mode}:{group_id}:{s.student_id}",
            )]
            for s in soloists
        ]
        back_cb = f"sp_brn:{mode}:{group.branch_id}" if group else "admin:students"
        buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
        await callback.message.edit_text(
            f"<b>Солисты — {group_name} ({len(soloists)}):</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_create_pair:"))
async def cb_admin_create_pair(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_service: StudentService,
) -> None:
    """Админ: выбор первого ученика для новой пары (из солистов группы).
    Дальше — стандартный поток partner_assign:<id>."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    back_cb = f"sp_brn:pairs:{group.branch_id}" if group else "admin:students"

    soloists = await student_service.soloists_in_group(group_id)
    if not soloists:
        await callback.message.edit_text(
            f"В группе «{group_name}» нет солистов, из которых можно собрать пару.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"admin_pair_lead:{group_id}:{s.student_id}")]
        for s in soloists
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"sp_grp:pairs:{group_id}")])
    await callback.message.edit_text(
        f"<b>Создать пару — {group_name}</b>\nВыберите первого ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_pair_lead:"))
async def cb_admin_pair_lead(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository,
    student_service: StudentService,
) -> None:
    """Админ: выбор лидера через «Создать пару» — как partner_assign, но помнит группу."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":")
    back_cb = f"sp_grp:pairs:{group_id}"
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    candidates = await student_service.partner_candidates_in_group(student, group_id)
    if not candidates:
        await callback.message.edit_text(
            "В этой группе нет других учеников для пары.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return
    await state.set_state(PartnerAssignStates.choosing_partner)
    await state.update_data(student_id=student_id, admin_pair_group_id=group_id)
    await callback.message.edit_text(
        f"<b>Выберите партнёра для «{student.name}».</b>\n"
        f"⚠️ — у ученика уже есть партнёр, старая связь будет разорвана.",
        reply_markup=kb_partner_candidates(candidates, student_id, cancel_cb=back_cb),
    )
    await callback.answer()


