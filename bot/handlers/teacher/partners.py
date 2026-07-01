from __future__ import annotations
"""
Педагог: «Мои пары», «Мои ученики (соло)», карточка ученика
и управление партнёром в рамках учеников своих групп.

Видимость ученика педагогу — через TeacherVisibilityService
(множество групп ученика пересекается с группами педагога).
"""
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, TeacherRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, LessonRepository, TeacherPeriodSubmissionRepository,
)
from bot.services import TeacherVisibilityService
from bot.states import PartnerAssignStates, TeacherRenameStudentStates
from bot.keyboards.teacher import (
    kb_my_student_card, kb_my_pair_card,
    kb_t_partner_candidates, kb_t_confirm,
)
from bot.handlers.common import show_card

logger = logging.getLogger(__name__)
router = Router(name="teacher_partners")


from bot.handlers.access import is_teacher as _is_teacher


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
    mine_by_id = {s.student_id: s for s in mine}
    mine_ids = set(mine_by_id)
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return
    # mine уже содержит hydrated group_ids — возьмём их оттуда.
    student.group_ids = mine_by_id[student.student_id].group_ids
    # Для back_cb выбираем группу, которая одновременно и у педагога, и у ученика.
    teacher_gids = await visibility.visible_group_ids(user.teacher_id)
    shared_gids = [gid for gid in student.group_ids if gid in teacher_gids]
    primary_gid = shared_gids[0] if shared_gids else ""

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
            f"t_pairs_grp:{primary_gid}" if primary_gid
            else "teacher:my_pairs"
        )
        kb = kb_my_pair_card(
            student.student_id, student.partner_id,
            back_cb=pairs_back_cb,
        ) if can_manage else InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data=pairs_back_cb)]]
        )
    else:
        back_cb = (
            f"t_solo_grp:{primary_gid}" if primary_gid
            else "teacher:my_soloists"
        )
        kb = kb_my_student_card(
            student.student_id,
            has_partner=bool(student.partner_id),
            can_manage=can_manage,
            back_cb=back_cb,
        )
    await show_card(callback, text, reply_markup=kb)


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


# ─── Занятия ученика за выбранный период ─────────────────────────────────────

def _stu_ids(lesson) -> set[str]:
    """Все student_id, задействованные в занятии."""
    ids = set()
    for sid in (lesson.student_1_id, lesson.student_2_id,
                lesson.student_3_id, lesson.student_4_id):
        if sid:
            ids.add(sid)
    if lesson.attendees:
        for part in lesson.attendees.replace("|", ",").split(","):
            sid = part.strip().split(":")[0].strip()
            if sid and sid.startswith("STU-"):
                ids.add(sid)
    return ids


@router.callback_query(F.data.startswith("t_stu_les:"))
async def cb_t_stu_lessons_months(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    months = sorted(
        {ls.date[:7] for ls in lessons if student_id in _stu_ids(ls)},
        reverse=True,
    )
    if not months:
        await callback.answer(f"Занятий с {student.name} не найдено.", show_alert=True)
        return

    from bot.utils.dates import display_period
    rows = [
        [InlineKeyboardButton(
            text=display_period(ym),
            callback_data=f"t_stu_les_m:{student_id}:{ym}",
        )]
        for ym in months
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")])
    await callback.message.edit_text(
        f"<b>📋 Занятия с {student.name}</b>\nВыберите период:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_stu_les_m:"))
async def cb_t_stu_lessons_list(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, ym = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    stu_lessons = sorted(
        [ls for ls in lessons if ls.date[:7] == ym and student_id in _stu_ids(ls)],
        key=lambda ls: ls.date,
    )
    back_cb = f"t_stu_les:{student_id}"

    if not stu_lessons:
        from bot.utils.dates import display_period
        await callback.message.edit_text(
            f"Занятий с {student_name} за {display_period(ym)} не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
            ]),
        )
        await callback.answer()
        return

    locked_period = bool(
        await submission_repo.get_by_teacher_and_period(user.teacher_id, ym)
    )
    total_min = sum(ls.duration_min for ls in stu_lessons)

    await state.update_data(t_stu_les_back=f"t_stu_les_m:{student_id}:{ym}")

    from bot.utils.dates import display_period, format_date_short_with_wd
    from bot.models.enums import LessonType
    rows = []
    for ls in stu_lessons:
        lock = "🔒 " if locked_period else ""
        date_s = format_date_short_with_wd(ls.date)
        if ls.type == LessonType.GROUP:
            who = "группа"
        else:
            names = [n for n in (ls.student_1_name, ls.student_2_name,
                                 ls.student_3_name, ls.student_4_name) if n]
            who = " + ".join(names) if names else student_name
        rows.append([InlineKeyboardButton(
            text=f"{lock}{date_s} · {ls.duration_min}м · {who}",
            callback_data=f"lesson_detail:{ls.lesson_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    lock_note = " · 🔒 период сдан" if locked_period else ""
    await callback.message.edit_text(
        f"<b>{student_name} · {display_period(ym)}{lock_note}</b>\n"
        f"Занятий: {len(stu_lessons)} · {total_min} мин",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_pair_les:"))
async def cb_t_pair_lessons_months(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, partner_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    partner = await student_repo.get_by_id(partner_id)
    pair_label = f"{student.name} + {partner.name}" if student and partner else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    months = sorted(
        {
            ls.date[:7] for ls in lessons
            if student_id in _stu_ids(ls) and partner_id in _stu_ids(ls)
        },
        reverse=True,
    )
    if not months:
        await callback.answer("Парных занятий не найдено.", show_alert=True)
        return

    from bot.utils.dates import display_period
    rows = [
        [InlineKeyboardButton(
            text=display_period(ym),
            callback_data=f"t_pair_les_m:{student_id}:{partner_id}:{ym}",
        )]
        for ym in months
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_pair_card:{student_id}")])
    await callback.message.edit_text(
        f"<b>📋 Занятия пары {pair_label}</b>\nВыберите период:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_pair_les_m:"))
async def cb_t_pair_lessons_list(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, partner_id, ym = callback.data.split(":", 3)
    student = await student_repo.get_by_id(student_id)
    partner = await student_repo.get_by_id(partner_id)
    pair_label = f"{student.name} + {partner.name}" if student and partner else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    pair_lessons = sorted(
        [
            ls for ls in lessons
            if ls.date[:7] == ym
            and student_id in _stu_ids(ls)
            and partner_id in _stu_ids(ls)
        ],
        key=lambda ls: ls.date,
    )
    back_cb = f"t_pair_les:{student_id}:{partner_id}"

    if not pair_lessons:
        from bot.utils.dates import display_period
        await callback.message.edit_text(
            f"Парных занятий за {display_period(ym)} не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
            ]),
        )
        await callback.answer()
        return

    locked_period = bool(
        await submission_repo.get_by_teacher_and_period(user.teacher_id, ym)
    )
    total_min = sum(ls.duration_min for ls in pair_lessons)
    await state.update_data(t_stu_les_back=f"t_pair_les_m:{student_id}:{partner_id}:{ym}")

    from bot.utils.dates import display_period, format_date_short_with_wd
    rows = []
    for ls in pair_lessons:
        lock = "🔒 " if locked_period else ""
        date_s = format_date_short_with_wd(ls.date)
        names = [n for n in (ls.student_1_name, ls.student_2_name,
                             ls.student_3_name, ls.student_4_name) if n]
        who = " + ".join(names) if names else pair_label
        rows.append([InlineKeyboardButton(
            text=f"{lock}{date_s} · {ls.duration_min}м · {who}",
            callback_data=f"lesson_detail:{ls.lesson_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    lock_note = " · 🔒 период сдан" if locked_period else ""
    await callback.message.edit_text(
        f"<b>{pair_label} · {display_period(ym)}{lock_note}</b>\n"
        f"Занятий: {len(pair_lessons)} · {total_min} мин",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()

