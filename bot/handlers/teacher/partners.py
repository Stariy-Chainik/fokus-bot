from __future__ import annotations
"""
Педагог: «Мои пары», «Мои ученики (соло)», карточка ученика
и управление партнёром в рамках учеников своих групп.

Видимость ученика педагогу — через TeacherVisibilityService
(student.group_id ∈ teacher_groups[teacher_id]).
"""
import logging
import uuid

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, TeacherRepository, UserRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository, StudentRequestRepository,
)
from bot.services import TeacherVisibilityService
from bot.states import PartnerAssignStates, TeacherAddStudentStates, TeacherRenameStudentStates
from bot.keyboards.teacher import (
    kb_my_student_card, kb_my_pair_card,
    kb_t_partner_candidates, kb_t_confirm,
)

logger = logging.getLogger(__name__)
router = Router(name="teacher_partners")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


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
        "<b>Мои солисты — выберите группу:</b>",
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
    buttons.append([InlineKeyboardButton(
        text="✨ Создать нового ученика", callback_data=f"t_add_new:{group_id}",
    )])
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
        "<b>Мои пары — выберите группу:</b>",
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

    grp_students = [s for s in mine if s.group_id == group_id]
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


# ─── Карточка ученика / пары ─────────────────────────────────────────────────

async def _render_student_card(
    callback: CallbackQuery, student_id: str, user: User,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
    back_to_pairs: bool = False,
) -> None:
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return

    if student.partner_id:
        partner = await student_repo.get_by_id(student.partner_id)
        partner_name = partner.name if partner else f"(удалён: {student.partner_id})"
        # Педагог может управлять парой только если партнёр тоже видим ему.
        can_manage = partner is not None and partner.student_id in mine_ids
        note = "" if can_manage else "\n\n⚠️ Партнёр у другого педагога — управляет админ."
    else:
        partner_name = "— (солист)"
        can_manage = True
        note = ""

    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n\n"
        f"Партнёр: {partner_name}"
        f"{note}"
    )
    if back_to_pairs and student.partner_id:
        pairs_back_cb = (
            f"t_pairs_grp:{student.group_id}" if student.group_id
            else "teacher:my_pairs"
        )
        kb = kb_my_pair_card(
            student.student_id, back_cb=pairs_back_cb,
        ) if can_manage else InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data=pairs_back_cb)]]
        )
    else:
        back_cb = (
            f"t_solo_grp:{student.group_id}" if student.group_id
            else "teacher:my_soloists"
        )
        kb = kb_my_student_card(
            student.student_id,
            has_partner=bool(student.partner_id),
            can_manage=can_manage,
            back_cb=back_cb,
        )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("t_student_card:"))
async def cb_student_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, visibility, back_to_pairs=False)


@router.callback_query(F.data.startswith("t_pair_card:"))
async def cb_pair_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, visibility, back_to_pairs=True)


# ─── Назначение партнёра (педагог) ───────────────────────────────────────────

@router.callback_query(F.data.startswith("t_partner_assign:") | F.data.startswith("t_cp_lead:"))
async def cb_partner_assign_start(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    student_repo: StudentRepository,
    visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    from_create_pair = callback.data.startswith("t_cp_lead:")
    student_id = callback.data.split(":", 1)[1]
    cancel_cb = "teacher:my_pairs" if from_create_pair else f"t_student_card:{student_id}"
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return

    # Если у ученика уже есть партнёр, и он НЕ видим педагогу — блокируем.
    if student.partner_id and student.partner_id not in mine_ids:
        await callback.answer("Партнёр у другого педагога — управляет админ.", show_alert=True)
        return

    # Кандидаты — все остальные видимые педагогу ученики, кроме текущего партнёра.
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    candidates = []
    for other in all_students:
        if other.student_id == student_id or other.student_id not in mine_ids:
            continue
        if other.student_id == student.partner_id:
            continue
        # Если у кандидата есть партнёр и он НЕ видим педагогу — пропускаем,
        # педагог не вправе рвать чужую пару.
        if other.partner_id and other.partner_id not in mine_ids:
            continue
        candidates.append((other, bool(other.partner_id)))

    if not candidates:
        await callback.message.edit_text(
            "Нет подходящих кандидатов среди ваших учеников.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=cancel_cb)],
            ]),
        )
        await callback.answer()
        return

    await state.set_state(PartnerAssignStates.choosing_partner)
    await state.update_data(t_student_id=student_id, t_from_create_pair=from_create_pair)
    await callback.message.edit_text(
        f"Выберите партнёра для «{student.name}».\n"
        f"⚠️ — у ученика уже есть партнёр (из ваших), старая связь будет разорвана.",
        reply_markup=kb_t_partner_candidates(candidates, student_id, cancel_cb=cancel_cb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_partner_pick:"), PartnerAssignStates.choosing_partner)
async def cb_partner_pick(
    callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository,
) -> None:
    partner_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    student_id = data.get("t_student_id", "")
    a = await student_repo.get_by_id(student_id)
    b = await student_repo.get_by_id(partner_id)
    if not a or not b:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    lines = ["Назначить партнёрами:", f"• {a.name}", f"• {b.name}"]
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

    cancel_cb = (
        "teacher:my_pairs" if data.get("t_from_create_pair") else f"t_student_card:{student_id}"
    )
    await state.update_data(t_partner_id=partner_id)
    await state.set_state(PartnerAssignStates.confirming)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_t_confirm("t_confirm_partner", cancel_cb),
    )
    await callback.answer()


@router.callback_query(F.data == "t_confirm_partner", PartnerAssignStates.confirming)
async def cb_partner_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    student_id = data.get("t_student_id", "")
    partner_id = data.get("t_partner_id", "")
    try:
        await student_repo.set_partner(student_id, partner_id)
        await callback.message.edit_text(
            "Партнёры назначены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
            ]),
        )
    except ValueError as exc:
        await callback.message.edit_text(
            f"Ошибка: {exc}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")],
            ]),
        )
    except Exception as exc:
        logger.error("Ошибка назначения партнёра (педагог): %s", exc)
        await callback.message.edit_text(
            "Не удалось назначить партнёра. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")],
            ]),
        )
    await callback.answer()


# ─── Снятие партнёра (педагог) ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("t_partner_clear:"))
async def cb_partner_clear_confirm(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student or not student.partner_id:
        await callback.answer("У ученика нет партнёра", show_alert=True)
        return

    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_ids = {s.student_id for s in mine}
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return
    if student.partner_id not in mine_ids:
        await callback.answer("Партнёр у другого педагога — управляет админ.", show_alert=True)
        return

    partner = await student_repo.get_by_id(student.partner_id)
    partner_name = partner.name if partner else student.partner_id
    await callback.message.edit_text(
        f"Убрать пару: «{student.name}» ↔ «{partner_name}»?",
        reply_markup=kb_t_confirm(
            f"t_confirm_partner_clear:{student_id}", f"t_student_card:{student_id}",
            confirm_text="❌ Убрать",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_confirm_partner_clear:"))
async def cb_partner_clear_do(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    try:
        await student_repo.clear_partner(student_id)
        await callback.message.edit_text(
            "Партнёр снят.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
            ]),
        )
    except Exception as exc:
        logger.error("Ошибка снятия партнёра (педагог): %s", exc)
        await callback.message.edit_text(
            "Не удалось снять партнёра.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")],
            ]),
        )
    await callback.answer()


# ─── Переименование ученика (педагог) ─────────────────────────────────────────

@router.callback_query(F.data.startswith("t_rename_student:"))
async def cb_rename_student_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    if not await visibility.is_visible(user.teacher_id, student_id):
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await state.set_state(TeacherRenameStudentStates.entering_name)
    await state.update_data(t_rename_student_id=student_id)
    await callback.message.edit_text(
        f"Текущее имя: <b>{student.name}</b>\n\nВведите новое имя (Фамилия Имя):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"t_student_card:{student_id}")],
        ]),
    )
    await callback.answer()


@router.message(TeacherRenameStudentStates.entering_name)
async def rename_student_save(
    message: Message, state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Имя не может быть пустым. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    student_id = data["t_rename_student_id"]
    ok = await student_repo.update_name(student_id, name)
    text = f"✅ Имя обновлено: <b>{name}</b>" if ok else "Ученик не найден."
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")],
        ]),
    )


# ─── Создание нового ученика через заявку админу ─────────────────────────────

@router.callback_query(F.data.startswith("t_add_new:"))
async def cb_add_new_student_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await state.set_state(TeacherAddStudentStates.searching)
    await state.update_data(t_add_query="", t_add_from_group=group_id)
    await callback.message.edit_text(
        "<b>Новый ученик</b>\n\n"
        "Введите <b>Фамилию и Имя через пробел</b> (ровно два слова, без отчества). "
        "Будет создана заявка администратору.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"t_solo_grp:{group_id}")],
        ]),
    )
    await callback.answer()


async def _delete_prev_search_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prev_id = data.get("t_add_last_msg_id")
    if prev_id:
        try:
            await message.bot.delete_message(message.chat.id, prev_id)
        except Exception:
            pass
    await state.update_data(t_add_last_msg_id=None)


@router.message(TeacherAddStudentStates.searching)
async def msg_add_student_search(
    message: Message, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_teacher(user):
        return
    query = (message.text or "").strip()
    await _delete_prev_search_reply(message, state)

    parts = query.split()
    if len(parts) != 2:
        reply = await message.answer(
            "❗ Введите <b>Фамилию и Имя через пробел</b> (ровно два слова, без отчества)."
        )
        await state.update_data(t_add_last_msg_id=reply.message_id)
        return

    normalized = " ".join(parts)
    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id

    preset_group_id = (await state.get_data()).get("t_add_from_group")
    group_line = ""
    if preset_group_id:
        group = await group_repo.get_by_id(preset_group_id)
        if group:
            branch = await branch_repo.get_by_id(group.branch_id)
            bname = branch.name if branch else "—"
            group_line = f"Группа: <b>{group.name}</b> (филиал «{bname}»)\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить заявку", callback_data="t_req_send")],
        [InlineKeyboardButton(text="« Отмена", callback_data="teacher:menu")],
    ])
    reply = await message.answer(
        "<b>Создать нового ученика</b>\n\n"
        f"Фамилия Имя: <b>{normalized}</b>\n"
        f"{group_line}"
        f"Педагог: <b>{teacher_name}</b>\n\n"
        "Заявка уйдёт администратору на одобрение.",
        reply_markup=kb,
    )
    await state.update_data(t_add_last_msg_id=reply.message_id, t_add_query=normalized)


async def _submit_new_student(
    callback: CallbackQuery, state: FSMContext, user: User,
    student_name: str, group_id: str,
    teacher_repo: TeacherRepository, user_repo: UserRepository,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    """Финализирует создание ученика: либо сразу (педагог-админ), либо заявкой."""
    # Shortcut: педагог-админ создаёт ученика сам, без заявки.
    # Видимость педагогу — через прикрепление к группе (если он к ней привязан).
    if user.is_admin:
        try:
            student = await student_repo.add(name=student_name)
            await student_repo.update_group(student.student_id, group_id)
        except Exception as exc:
            logger.error("Ошибка self-service создания ученика педагогом-админом: %s", exc)
            await callback.answer("Не удалось создать ученика. Попробуйте позже.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text(
            f"✅ Ученик <b>{student.name}</b> создан в выбранной группе.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К солистам", callback_data=f"t_solo_grp:{group_id}")],
                [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
        return

    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id

    admins = [u for u in await user_repo.get_all() if u.is_admin]
    if not admins:
        await callback.answer("В системе нет администратора — заявка не может быть обработана.", show_alert=True)
        return

    group = await group_repo.get_by_id(group_id)
    gname = group.name if group else group_id
    branch = await branch_repo.get_by_id(group.branch_id) if group else None
    bname = branch.name if branch else "—"

    req_id = uuid.uuid4().hex[:8]
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать и привязать", callback_data=f"req_approve:{req_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req_reject:{req_id}")],
    ])
    notify_text = (
        f"📝 <b>Заявка на создание ученика</b>\n\n"
        f"Педагог: <b>{teacher_name}</b>\n"
        f"Фамилия Имя: <b>{student_name}</b>\n"
        f"Группа: <b>{gname}</b> (филиал «{bname}»)"
    )
    admin_msgs: list[tuple[int, int]] = []
    for admin in admins:
        try:
            msg = await callback.bot.send_message(admin.tg_id, notify_text, reply_markup=admin_kb)
            admin_msgs.append((msg.chat.id, msg.message_id))
        except Exception:
            pass

    try:
        await student_request_repo.add(
            request_id=req_id,
            teacher_id=user.teacher_id,
            teacher_tg_id=callback.from_user.id,
            teacher_name=teacher_name,
            student_name=student_name,
            group_id=group_id,
            admin_msgs=admin_msgs,
        )
    except Exception as exc:
        logger.error("Не удалось сохранить заявку в Sheets: %s", exc)
        await callback.answer("Не удалось сохранить заявку. Попробуйте позже.", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>Заявка отправлена администратору</b>\n\n"
        f"Ученик: <b>{student_name}</b>\n"
        f"Группа: <b>{gname}</b> (филиал «{bname}»)\n\n"
        "Вы получите уведомление после обработки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "t_req_send", TeacherAddStudentStates.searching)
async def cb_request_new_student_send(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, user_repo: UserRepository,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
) -> None:
    """Отправить заявку. Если группа уже известна (педагог пришёл из экрана группы) —
    отправляем без повторного выбора. Иначе показываем выбор группы (fallback)."""
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    student_name = " ".join((data.get("t_add_query") or "").split())
    parts = student_name.split()
    if len(parts) != 2:
        await callback.answer(
            "Нужно ввести ровно Фамилию и Имя (без отчества). Отправьте сообщением заново.",
            show_alert=True,
        )
        return

    preset_group_id = data.get("t_add_from_group")
    if preset_group_id:
        await _submit_new_student(
            callback, state, user, student_name, preset_group_id,
            teacher_repo, user_repo, student_repo,
            student_request_repo, group_repo, branch_repo,
        )
        return

    # Fallback: группа неизвестна — показать выбор (только группы педагога).
    my_group_ids = set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))
    all_groups = await group_repo.get_all()
    my_groups = sorted(
        [g for g in all_groups if g.group_id in my_group_ids], key=lambda g: g.name,
    )

    if not my_groups:
        await callback.answer(
            "Нельзя создать ученика: у вас нет тренировочных групп. Обратитесь к администратору.",
            show_alert=True,
        )
        return

    await state.update_data(t_new_student_name=student_name)
    await state.set_state(TeacherAddStudentStates.choosing_group)

    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    by_branch: dict[str, list] = {}
    for g in my_groups:
        by_branch.setdefault(g.branch_id, []).append(g)

    rows = []
    for bid, groups in by_branch.items():
        bname = branches.get(bid, bid)
        for g in groups:
            rows.append([InlineKeyboardButton(
                text=f"🏢 {bname} — {g.name}",
                callback_data=f"t_new_pick_group:{g.group_id}",
            )])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="teacher:menu")])

    await callback.message.edit_text(
        f"<b>Новый ученик: {student_name}</b>\n\nВыберите тренировочную группу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_new_pick_group:"), TeacherAddStudentStates.choosing_group)
async def cb_request_new_student_with_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, user_repo: UserRepository,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    student_name = " ".join((data.get("t_new_student_name") or "").split())
    parts = student_name.split()
    if len(parts) != 2:
        await callback.answer("Имя ученика потерялось. Начните заново.", show_alert=True)
        await state.clear()
        return

    await _submit_new_student(
        callback, state, user, student_name, group_id,
        teacher_repo, user_repo, student_repo,
        student_request_repo, group_repo, branch_repo,
    )
