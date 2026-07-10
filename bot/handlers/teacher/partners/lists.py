from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    GroupRepository, TeacherGroupRepository,
)
from bot.services import TeacherVisibilityService
from bot.handlers.access import is_teacher as _is_teacher

from ._base import router

logger = logging.getLogger(__name__)


# ─── Мои солисты: выбор группы → список ──────────────────────────────────────

@router.callback_query(F.data == "teacher:my_soloists")
async def cb_my_soloists_groups(
    callback: CallbackQuery,
    user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    gids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    groups = sorted(
        [g for g in await group_repo.get_all() if g.group_id in gids],
        key=lambda g: (g.sort_order, g.name),
    )
    if not groups:
        await callback.message.edit_text(
            "У вас нет групп.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=g.name, callback_data=f"t_solo_grp:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    await callback.message.edit_text(
        "<b>Солисты — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_solo_grp:"))
async def cb_my_soloists_list(
    callback: CallbackQuery,
    user: User | None,
    visibility: TeacherVisibilityService,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    students = await visibility.students_in_group_for_teacher(user.teacher_id, group_id)
    soloists = [s for s in students if not s.partner_id]

    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"t_student_card:{s.student_id}")]
        for s in soloists
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_soloists")])

    header = (
        f"<b>Солисты — {group_name}</b> ({len(soloists)}):"
        if soloists
        else f"В группе «{group_name}» солистов нет."
    )
    await callback.message.edit_text(
        header,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ─── Мои пары: выбор группы → список ─────────────────────────────────────────

@router.callback_query(F.data == "teacher:my_pairs")
async def cb_my_pairs_groups(
    callback: CallbackQuery,
    user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    gids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    groups = sorted(
        [g for g in await group_repo.get_all() if g.group_id in gids],
        key=lambda g: (g.sort_order, g.name),
    )
    if not groups:
        await callback.message.edit_text(
            "У вас нет групп.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=g.name, callback_data=f"t_pairs_grp:{g.group_id}")]
        for g in groups
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    await callback.message.edit_text(
        "<b>Пары — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_pairs_grp:"))
async def cb_my_pairs_list(
    callback: CallbackQuery,
    user: User | None,
    visibility: TeacherVisibilityService,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}

    grp_students = [s for s in mine if group_id in s.group_ids]
    by_id = {s.student_id: s for s in mine}
    seen: set[tuple[str, str]] = set()
    pairs = []
    for s in grp_students:
        if not s.partner_id or s.partner_id not in mine_ids:
            continue
        partner = by_id.get(s.partner_id)
        if not partner:
            continue
        key = tuple(sorted([s.student_id, partner.student_id]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((s, partner))

    if not pairs:
        await callback.message.edit_text(
            f"В группе «{group_name}» пар нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать пару", callback_data="teacher:create_pair")],
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_pairs")],
            ]),
        )
        await callback.answer()
        return

    buttons = []
    for a, b in pairs:
        buttons.append([InlineKeyboardButton(
            text=f"{a.name} ↔ {b.name}",
            callback_data=f"t_pair_card:{a.student_id}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Создать пару", callback_data="teacher:create_pair")])
    buttons.append([InlineKeyboardButton(text="❌ Удалить пару", callback_data="teacher:pair_clear_pick")])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_pairs")])
    await callback.message.edit_text(
        f"<b>Пары — {group_name}</b> ({len(pairs)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:pair_clear_pick")
async def cb_pair_clear_pick(
    callback: CallbackQuery, user: User | None,
    visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}
    seen: set[tuple[str, str]] = set()
    pairs = []
    by_id = {s.student_id: s for s in mine}
    for s in mine:
        if not s.partner_id or s.partner_id not in mine_ids:
            continue
        partner = by_id[s.partner_id]
        key = tuple(sorted([s.student_id, partner.student_id]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((s, partner))
    if not pairs:
        await callback.answer("У вас нет пар.", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"{a.name} ↔ {b.name}",
            callback_data=f"t_partner_clear:{a.student_id}",
        )]
        for a, b in pairs
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_pairs")])
    await callback.message.edit_text(
        "Выберите пару для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:create_pair")
async def cb_create_pair_start(
    callback: CallbackQuery,
    user: User | None,
    visibility: TeacherVisibilityService,
) -> None:
    """Шаг 1 «Создать пару»: выбор первого ученика из видимых педагогу.
    Вторым шагом переиспользуется существующий t_partner_assign:<id>.
    """
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}
    # Кандидаты-лидеры: солисты и те, у кого партнёр тоже видим педагогу
    # (иначе управление — у другого педагога/админа).
    leaders = [s for s in mine if not s.partner_id or s.partner_id in mine_ids]
    if not leaders:
        await callback.message.edit_text(
            "Нет доступных учеников для создания пары.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_pairs")],
            ]),
        )
        await callback.answer()
        return

    buttons = []
    for s in leaders:
        mark = " 💃" if s.partner_id else ""
        buttons.append([InlineKeyboardButton(
            text=f"{s.name}{mark}", callback_data=f"t_cp_lead:{s.student_id}",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:my_pairs")])
    await callback.message.edit_text(
        "<b>Создать пару</b>\nВыберите первого ученика:\n"
        "💃 — у ученика уже есть партнёр, старая связь будет разорвана.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


