from __future__ import annotations
"""
Педагог: «Мои пары», «Мои ученики (соло)», карточка ученика
и управление партнёром в рамках своих учеников.
"""
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import StudentRepository, TeacherStudentRepository
from bot.states import PartnerAssignStates, TeacherAddStudentStates
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


# ─── Мои ученики (соло) ──────────────────────────────────────────────────────

@router.callback_query(F.data == "teacher:my_students")
async def cb_my_students(
    callback: CallbackQuery,
    user: User | None,
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
    soloists = [s for s in mine if not s.partner_id]
    if not soloists:
        await callback.message.edit_text(
            "У вас нет учеников-солистов (все в парах или пусто).",
            reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
        return

    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"t_student_card:{s.student_id}")]
        for s in soloists
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    await callback.message.edit_text(
        f"Ваши ученики-солисты ({len(soloists)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ─── Мои пары ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "teacher:my_pairs")
async def cb_my_pairs(
    callback: CallbackQuery,
    user: User | None,
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mine = await _linked_students_of(user.teacher_id, ts_repo, student_repo)
    mine_ids = {s.student_id for s in mine}

    # Пары, где ОБА партнёра привязаны к этому педагогу.
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
        await callback.message.edit_text(
            "У вас пока нет пар среди учеников.",
            reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
        return

    buttons = []
    for a, b in pairs:
        buttons.append([InlineKeyboardButton(
            text=f"{a.name} ↔ {b.name}",
            callback_data=f"t_pair_card:{a.student_id}",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    await callback.message.edit_text(
        f"Ваши пары ({len(pairs)}):",
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
        kb = kb_my_student_card(student.student_id, has_partner=bool(student.partner_id), can_manage=can_manage)
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

@router.callback_query(F.data.startswith("t_partner_assign:"))
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
    student_id = callback.data.split(":", 1)[1]
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
                [InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")],
            ]),
        )
        await callback.answer()
        return

    await state.set_state(PartnerAssignStates.choosing_partner)
    await state.update_data(t_student_id=student_id)
    await callback.message.edit_text(
        f"Выберите партнёра для «{student.name}».\n"
        f"⚠️ — у ученика уже есть партнёр (из ваших), старая связь будет разорвана.",
        reply_markup=kb_t_partner_candidates(candidates, student_id),
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

    await state.update_data(t_partner_id=partner_id)
    await state.set_state(PartnerAssignStates.confirming)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_t_confirm("t_confirm_partner", f"t_student_card:{student_id}"),
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
                [InlineKeyboardButton(text="« К ученику", callback_data=f"t_student_card:{student_id}")],
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
            f"t_confirm_partner_clear:{student_id}", f"t_student_card:{student_id}"
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
                [InlineKeyboardButton(text="« К ученику", callback_data=f"t_student_card:{student_id}")],
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


# ─── Добавить ученика в свой список (привязка педагог↔ученик) ────────────────

PAGE_SIZE = 10


@router.callback_query(F.data == "teacher:add_student")
async def cb_add_student_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(TeacherAddStudentStates.searching)
    await state.update_data(t_add_query="")
    await callback.message.edit_text(
        "Введите фамилию или первые буквы ученика, которого хотите добавить в свой список.\n"
        "Чтобы показать всех — отправьте <b>*</b>"
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


@router.message(TeacherAddStudentStates.searching)
async def msg_add_student_search(
    message: Message, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        return
    query = (message.text or "").strip()
    if query == "*":
        query = ""
    available = await _available_to_add(user, ts_repo, student_repo)
    if query:
        q = query.lower()
        available = [s for s in available if q in s.name.lower()]
    if not available:
        await message.answer(
            "Никого не найдено. Если ученика нет в школе — обратитесь к администратору."
        )
        return
    await state.update_data(t_add_query=query)
    label = f"Найдено: {len(available)}" if query else f"Доступно: {len(available)}"
    await message.answer(
        f"{label}. Выберите ученика для добавления:",
        reply_markup=_build_add_candidates_kb(available, page=0, total=len(available)),
    )


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


@router.callback_query(F.data.startswith("t_add_pick:"), TeacherAddStudentStates.searching)
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
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="teacher:add_student")],
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


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
            f"t_confirm_unlink_self:{student_id}", f"t_student_card:{student_id}"
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
