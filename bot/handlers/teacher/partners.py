from __future__ import annotations
"""
Педагог: «Мои пары», «Мои ученики (соло)», карточка ученика
и управление партнёром в рамках своих учеников.
"""
import logging
import uuid

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, TeacherStudentRepository, TeacherRepository, UserRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository, StudentRequestRepository,
)
from bot.states import PartnerAssignStates, TeacherAddStudentStates, TeacherRenameStudentStates
from bot.keyboards.teacher import (
    kb_teacher_menu, kb_my_student_card, kb_my_pair_card,
    kb_t_partner_candidates, kb_t_confirm,
)

logger = logging.getLogger(__name__)
router = Router(name="teacher_partners")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _linked_students_of(teacher_id: str, ts_repo, student_repo):
    ids = set(await ts_repo.get_students_for_teacher(teacher_id))
    return sorted([s for s in await student_repo.get_all() if s.student_id in ids], key=lambda s: s.name)


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
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
    soloists = [s for s in mine if not s.partner_id and s.group_id == group_id]

    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"t_student_card:{s.student_id}")]
        for s in soloists
    ]
    buttons.append([InlineKeyboardButton(text="➕ Добавить ученика", callback_data=f"t_add_from_grp:{group_id}")])
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
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
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
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
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
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
) -> None:
    """Шаг 1 «Создать пару»: выбор первого ученика из своего списка.
    Вторым шагом переиспользуется существующий t_partner_assign:<id>.
    """
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
    mine_ids = {s.student_id for s in mine}
    # Кандидаты-лидеры: солисты и те, у кого партнёр тоже в списке педагога
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
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
    back_to_pairs: bool = False,
) -> None:
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не привязан к вам", show_alert=True)
        return

    if student.partner_id:
        partner = await student_repo.get_by_id(student.partner_id)
        partner_name = partner.name if partner else f"(удалён: {student.partner_id})"
        # Педагог может управлять пары только если партнёр тоже в его списке.
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
        kb = kb_my_pair_card(student.student_id) if can_manage else InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Назад к парам", callback_data="teacher:my_pairs")]]
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
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, ts_repo, back_to_pairs=False)


@router.callback_query(F.data.startswith("t_pair_card:"))
async def cb_pair_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, ts_repo, back_to_pairs=True)


# ─── Назначение партнёра (педагог) ───────────────────────────────────────────

@router.callback_query(F.data.startswith("t_partner_assign:") | F.data.startswith("t_cp_lead:"))
async def cb_partner_assign_start(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    student_repo: StudentRepository,
    ts_repo: TeacherStudentRepository,
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

    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не привязан к вам", show_alert=True)
        return

    # Если у ученика уже есть партнёр, и он НЕ в списке педагога — блокируем.
    if student.partner_id and student.partner_id not in mine_ids:
        await callback.answer("Партнёр у другого педагога — управляет админ.", show_alert=True)
        return

    # Кандидаты — все остальные ученики этого педагога, кроме текущего партнёра.
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    candidates = []
    for other in all_students:
        if other.student_id == student_id or other.student_id not in mine_ids:
            continue
        if other.student_id == student.partner_id:
            continue
        # Если у кандидата есть партнёр и он НЕ ученик педагога — пропускаем,
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
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student or not student.partner_id:
        await callback.answer("У ученика нет партнёра", show_alert=True)
        return

    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не привязан к вам", show_alert=True)
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
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    if student_id not in mine_ids:
        await callback.answer("Ученик не привязан к вам", show_alert=True)
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


# ─── Добавить ученика в свой список (привязка педагог↔ученик) ────────────────

PAGE_SIZE = 10


async def _candidates_for_add(
    teacher_id: str, group_id: str,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
):
    mine_ids = set(await ts_repo.get_students_for_teacher(teacher_id))
    return sorted(
        [
            s for s in await student_repo.get_all()
            if s.group_id == group_id and s.student_id not in mine_ids
        ],
        key=lambda s: s.name,
    )


def _kb_add_multi_select(students, selected_ids: set, group_id: str) -> InlineKeyboardMarkup:
    rows = []
    for s in students:
        mark = "✅" if s.student_id in selected_ids else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {s.name}", callback_data=f"t_add_tg:{s.student_id}",
        )])
    if students:
        all_sel = len(selected_ids) == len(students)
        toggle_all = "◻️ Снять всех" if all_sel else "☑️ Отметить всех"
        rows.append([InlineKeyboardButton(text=toggle_all, callback_data="t_add_all_tg")])
    rows.append([InlineKeyboardButton(
        text=f"💾 Добавить ({len(selected_ids)})", callback_data="t_add_confirm",
    )])
    rows.append([InlineKeyboardButton(
        text="📝 Новый (запрос админу)", callback_data=f"t_add_new:{group_id}",
    )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_solo_grp:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("t_add_from_grp:"))
async def cb_add_student_from_group(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    candidates = await _candidates_for_add(user.teacher_id, group_id, ts_repo, student_repo)

    if not candidates:
        await state.clear()
        await callback.message.edit_text(
            f"В группе «{group_name}» нет учеников, которых можно добавить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📝 Новый (запрос админу)", callback_data=f"t_add_new:{group_id}")],
                [InlineKeyboardButton(text="« Назад", callback_data=f"t_solo_grp:{group_id}")],
            ]),
        )
        await callback.answer()
        return

    await state.set_state(TeacherAddStudentStates.multi_selecting)
    await state.update_data(t_add_group_id=group_id, t_add_selected=[])
    await callback.message.edit_text(
        f"<b>Добавить в группу «{group_name}»</b>\n"
        f"Отметьте учеников (доступно: {len(candidates)}), затем «Добавить»:",
        reply_markup=_kb_add_multi_select(candidates, set(), group_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_add_tg:"), TeacherAddStudentStates.multi_selecting)
async def cb_add_toggle(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer()
        return
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    group_id = data.get("t_add_group_id")
    if not group_id:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        return
    selected = set(data.get("t_add_selected") or [])
    selected.symmetric_difference_update({student_id})
    await state.update_data(t_add_selected=list(selected))
    candidates = await _candidates_for_add(user.teacher_id, group_id, ts_repo, student_repo)
    await callback.message.edit_reply_markup(
        reply_markup=_kb_add_multi_select(candidates, selected, group_id),
    )
    await callback.answer()


@router.callback_query(F.data == "t_add_all_tg", TeacherAddStudentStates.multi_selecting)
async def cb_add_toggle_all(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer()
        return
    data = await state.get_data()
    group_id = data.get("t_add_group_id")
    if not group_id:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        return
    candidates = await _candidates_for_add(user.teacher_id, group_id, ts_repo, student_repo)
    selected = set(data.get("t_add_selected") or [])
    all_ids = {s.student_id for s in candidates}
    new_selected = set() if selected == all_ids else all_ids
    await state.update_data(t_add_selected=list(new_selected))
    await callback.message.edit_reply_markup(
        reply_markup=_kb_add_multi_select(candidates, new_selected, group_id),
    )
    await callback.answer()


@router.callback_query(F.data == "t_add_confirm", TeacherAddStudentStates.multi_selecting)
async def cb_add_confirm(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    group_id = data.get("t_add_group_id")
    selected = list(dict.fromkeys(data.get("t_add_selected") or []))
    if not group_id:
        await callback.answer("Сессия истекла", show_alert=True)
        return
    if not selected:
        await callback.answer("Никого не выбрали", show_alert=True)
        return
    added, skipped, failed = 0, 0, 0
    for sid in selected:
        try:
            if await ts_repo.exists(user.teacher_id, sid):
                skipped += 1
            else:
                await ts_repo.add(user.teacher_id, sid)
                added += 1
        except Exception as exc:
            logger.error("Ошибка привязки ученика %s к педагогу: %s", sid, exc)
            failed += 1
    await state.clear()
    group = await group_repo.get_by_id(group_id)
    group_name = group.name if group else group_id
    parts = [f"✅ Добавлено: <b>{added}</b>"]
    if skipped:
        parts.append(f"уже было: {skipped}")
    if failed:
        parts.append(f"ошибок: {failed}")
    await callback.message.edit_text(
        f"Группа «{group_name}»\n" + ", ".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё", callback_data=f"t_add_from_grp:{group_id}")],
            [InlineKeyboardButton(text="« К солистам", callback_data=f"t_solo_grp:{group_id}")],
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


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
        "Если такого ученика нет в школе — будет создана заявка администратору.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"t_add_from_grp:{group_id}")],
        ]),
    )
    await callback.answer()


def _build_add_candidates_kb(students: list, page: int, total: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    page_slice = students[start:start + PAGE_SIZE]
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"t_add_pick:{s.student_id}")]
        for s in page_slice
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Пред.", callback_data=f"t_add_page:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="След. →", callback_data=f"t_add_page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data="teacher:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _available_to_add(user: User, ts_repo: TeacherStudentRepository, student_repo: StudentRepository) -> list:
    """Все ученики школы, кроме уже привязанных к этому педагогу."""
    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    return [s for s in all_students if s.student_id not in mine_ids]


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
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
    teacher_repo: TeacherRepository,
) -> None:
    if not _is_teacher(user):
        return
    query = (message.text or "").strip()
    await _delete_prev_search_reply(message, state)

    # Вариант 1: ручной просмотр всех учеников
    if query == "*":
        available = await _available_to_add(user, ts_repo, student_repo)
        if not available:
            reply = await message.answer(
                "В школе нет учеников, доступных для добавления "
                "(все уже в вашем списке или база пустая)."
            )
            await state.update_data(t_add_last_msg_id=reply.message_id, t_add_query="")
            return
        reply = await message.answer(
            f"Доступно: {len(available)}. Выберите ученика для добавления:",
            reply_markup=_build_add_candidates_kb(available, page=0, total=len(available)),
        )
        await state.update_data(t_add_last_msg_id=reply.message_id, t_add_query="")
        return

    # Вариант 2: точный ввод «Фамилия Имя»
    parts = query.split()
    if len(parts) != 2:
        reply = await message.answer(
            "❗ Введите <b>Фамилию и Имя через пробел</b> (ровно два слова, без отчества), "
            "либо <b>*</b> для ручного поиска."
        )
        await state.update_data(t_add_last_msg_id=reply.message_id)
        return

    normalized = " ".join(parts)
    ql = normalized.lower()
    all_students = await student_repo.get_all()
    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))

    # Уже в списке педагога
    already_mine = [s for s in all_students if s.student_id in mine_ids and ql in s.name.lower()]
    if already_mine:
        names = ", ".join(s.name for s in already_mine)
        reply = await message.answer(
            f"Уже в вашем списке: {names}.\nВведите другой запрос или <b>*</b> для всех."
        )
        await state.update_data(t_add_last_msg_id=reply.message_id)
        return

    # Есть в школе, но не у этого педагога — показываем для привязки
    matching_school = [s for s in all_students if s.student_id not in mine_ids and ql in s.name.lower()]
    if matching_school:
        reply = await message.answer(
            f"Найдено: {len(matching_school)}. Выберите ученика для добавления:",
            reply_markup=_build_add_candidates_kb(matching_school, page=0, total=len(matching_school)),
        )
        await state.update_data(t_add_last_msg_id=reply.message_id, t_add_query=normalized)
        return

    # В школе нет — карточка для подтверждения педагогом
    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Создать и привязать", callback_data="t_req_send")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="teacher:menu")],
    ])
    reply = await message.answer(
        "<b>Ученик не найден в школе.</b>\n"
        "Проверьте карточку и подтвердите отправку заявки администратору:\n\n"
        f"Фамилия Имя: <b>{normalized}</b>\n"
        f"Педагог: <b>{teacher_name}</b>",
        reply_markup=kb,
    )
    await state.update_data(t_add_last_msg_id=reply.message_id, t_add_query=normalized)


@router.callback_query(F.data.startswith("t_add_page:"), TeacherAddStudentStates.searching)
async def cb_add_student_page(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        return
    page = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    query = (data.get("t_add_query") or "").lower()
    available = await _available_to_add(user, ts_repo, student_repo)
    if query:
        available = [s for s in available if query in s.name.lower()]
    await callback.message.edit_reply_markup(
        reply_markup=_build_add_candidates_kb(available, page=page, total=len(available))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_add_pick:"))
async def cb_add_student_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    # Возврат — в ту группу, откуда пришёл педагог (если известна); иначе по group_id ученика.
    data = await state.get_data()
    from_group_id = data.get("t_add_from_group") or student.group_id
    await state.clear()
    try:
        if await ts_repo.exists(user.teacher_id, student_id):
            text = f"«{student.name}» уже в вашем списке."
        else:
            await ts_repo.add(user.teacher_id, student_id)
            text = f"«{student.name}» добавлен в ваш список."
    except Exception as exc:
        logger.error("Ошибка привязки ученика к педагогу (self-service): %s", exc)
        text = "Не удалось добавить ученика. Попробуйте позже."
    add_more_cb = (
        f"t_add_from_grp:{from_group_id}" if from_group_id else "teacher:my_soloists"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё", callback_data=add_more_cb)],
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


# ─── Заявка админу на создание нового ученика ────────────────────────────────

@router.callback_query(F.data == "t_req_send", TeacherAddStudentStates.searching)
async def cb_request_new_student_pick_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
) -> None:
    """Перед отправкой заявки — педагог выбирает группу для нового ученика."""
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

    # Сохраним имя — понадобится после выбора группы
    await state.update_data(t_new_student_name=student_name)
    await state.set_state(TeacherAddStudentStates.choosing_group)

    # Группируем по филиалу для удобства (по тексту)
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
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
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

    # Shortcut: педагог-админ создаёт ученика сам, без заявки
    if user.is_admin:
        try:
            student = await student_repo.add(name=student_name)
            await student_repo.update_group(student.student_id, group_id)
            await ts_repo.add(user.teacher_id, student.student_id)
        except Exception as exc:
            logger.error("Ошибка self-service создания ученика педагогом-админом: %s", exc)
            await callback.answer("Не удалось создать ученика. Попробуйте позже.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text(
            f"✅ Ученик <b>{student.name}</b> создан и добавлен в ваш список.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить ещё", callback_data=f"t_add_from_grp:{group_id}")],
                [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
            ]),
        )
        group = await group_repo.get_by_id(group_id)
        bname = "—"
        if group:
            branch = await branch_repo.get_by_id(group.branch_id)
            bname = branch.name if branch else "—"
        gname = group.name if group else group_id
        await callback.answer(
            f"Ученик «{student.name}» добавлен в группу «{gname}» (филиал «{bname}»)",
            show_alert=True,
        )
        return

    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id

    admins = [u for u in await user_repo.get_all() if u.is_admin]
    if not admins:
        await callback.answer("В системе нет администратора — заявка не может быть обработана.", show_alert=True)
        return

    req_id = uuid.uuid4().hex[:8]
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Создать и привязать", callback_data=f"req_approve:{req_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req_reject:{req_id}")],
    ])
    notify_text = (
        f"📝 <b>Заявка на создание ученика</b>\n\n"
        f"Педагог: <b>{teacher_name}</b>\n"
        f"Фамилия Имя: <b>{student_name}</b>\n"
        f"Группа: <code>{group_id}</code>"
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
        await callback.answer(
            "Не удалось сохранить заявку. Попробуйте позже.", show_alert=True,
        )
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ Ваша заявка отправлена администратору.\n"
        "Вы получите уведомление после обработки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    group = await group_repo.get_by_id(group_id)
    bname = "—"
    if group:
        branch = await branch_repo.get_by_id(group.branch_id)
        bname = branch.name if branch else "—"
    gname = group.name if group else group_id
    await callback.answer(
        f"Заявка: «{student_name}» → группа «{gname}» (филиал «{bname}»)",
        show_alert=True,
    )


# ─── Убрать ученика из своего списка ─────────────────────────────────────────

@router.callback_query(F.data.startswith("t_unlink_self:"))
async def cb_unlink_self_confirm(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    mine_ids = set(await ts_repo.get_students_for_teacher(user.teacher_id))
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашем списке", show_alert=True)
        return

    # Предупреждение: если у ученика есть партнёр, а после отвязки у них
    # не останется общего педагога — пара «потеряет площадку».
    warning = ""
    if student.partner_id:
        partner_teachers = set(await ts_repo.get_teachers_for_student(student.partner_id))
        student_teachers_after = set(await ts_repo.get_teachers_for_student(student_id)) - {user.teacher_id}
        if not (student_teachers_after & partner_teachers):
            partner = await student_repo.get_by_id(student.partner_id)
            partner_name = partner.name if partner else student.partner_id
            warning = (
                f"\n\n⚠️ У «{student.name}» останется партнёр «{partner_name}», "
                f"но после отвязки у них не будет общего педагога."
            )

    await callback.message.edit_text(
        f"Убрать «{student.name}» из вашего списка учеников?{warning}",
        reply_markup=kb_t_confirm(
            f"t_confirm_unlink_self:{student_id}", f"t_student_card:{student_id}",
            confirm_text="🚪 Убрать",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_confirm_unlink_self:"))
async def cb_unlink_self_do(
    callback: CallbackQuery, user: User | None,
    ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    try:
        ok = await ts_repo.remove(user.teacher_id, student_id)
        text = "Ученик убран из вашего списка." if ok else "Связь не найдена."
    except Exception as exc:
        logger.error("Ошибка отвязки ученика (педагог): %s", exc)
        text = "Не удалось убрать. Попробуйте позже."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()
