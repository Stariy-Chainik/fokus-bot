from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, TeacherRepository, TeacherStudentRepository, UserRepository,
    GroupRepository, BranchRepository,
)
from bot.states import AddStudentStates, StudentListStates, PartnerAssignStates
from bot.keyboards.admin import (
    kb_students_menu, kb_teacher_list, kb_student_list,
    kb_student_paged, kb_student_card, kb_partner_candidates,
    kb_confirm, kb_back, _STUDENT_PAGE_SIZE,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_students")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


@router.callback_query(F.data == "admin:students")
async def cb_students_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("<b>Управление учениками:</b>", reply_markup=kb_students_menu())
    await callback.answer()


# ─── Все пары и все солисты (школа целиком) ─────────────────────────────────

@router.callback_query(F.data == "students:all_pairs")
async def cb_all_pairs(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    all_students = await student_repo.get_all()
    by_id = {s.student_id: s for s in all_students}
    seen: set[tuple[str, str]] = set()
    pairs = []
    for s in all_students:
        if not s.partner_id:
            continue
        partner = by_id.get(s.partner_id)
        if not partner:
            continue
        key = tuple(sorted([s.student_id, partner.student_id]))
        if key in seen:
            continue
        seen.add(key)
        a, b = (s, partner) if s.name <= partner.name else (partner, s)
        pairs.append((a, b))

    if not pairs:
        await callback.message.edit_text("Пар пока нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return

    pairs.sort(key=lambda p: p[0].name)
    buttons = []
    for a, b in pairs:
        buttons.append([InlineKeyboardButton(
            text=f"{a.name} ↔ {b.name}",
            callback_data=f"student_card:{a.student_id}",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    await callback.message.edit_text(
        f"<b>Все пары ({len(pairs)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "students:all_soloists")
async def cb_all_soloists(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    soloists = [s for s in all_students if not s.partner_id]
    if not soloists:
        await callback.message.edit_text("Солистов нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return

    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"student_card:{s.student_id}")]
        for s in soloists
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    await callback.message.edit_text(
        f"<b>Все солисты ({len(soloists)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ─── Список учеников с поиском ───────────────────────────────────────────────

def _filter_and_page(students: list, query: str, page: int):
    if query:
        filtered = [s for s in students if query.lower() in s.name.lower()]
    else:
        filtered = students
    start = page * _STUDENT_PAGE_SIZE
    return filtered[start:start + _STUDENT_PAGE_SIZE], len(filtered)


@router.callback_query(F.data == "students:list")
async def cb_students_list(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(StudentListStates.searching)
    await state.update_data(student_query="")
    await callback.message.edit_text(
        "<b>Поиск ученика</b>\n"
        "Введите имя или часть имени ученика для поиска.\n"
        "Чтобы показать всех — отправьте <b>*</b>"
    )
    await callback.answer()


@router.message(StudentListStates.searching)
async def handle_student_search(
    message: Message, state: FSMContext, student_repo: StudentRepository,
) -> None:
    query = message.text.strip() if message.text else ""
    if query == "*":
        query = ""
    await state.update_data(student_query=query)
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    page_students, total = _filter_and_page(all_students, query, 0)
    if not page_students:
        await message.answer("Ничего не найдено. Попробуйте другой запрос.")
        return
    label = f"Найдено: {total}" if query else f"Всего учеников: {total}"
    await message.answer(
        f"<b>{label}. Страница 1:</b>",
        reply_markup=kb_student_paged(page_students, 0, total, query),
    )


@router.callback_query(F.data.startswith("spage:"))
async def cb_student_page(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    query = parts[1].replace("_", ":") if parts[1] != "" else ""
    page = int(parts[2])
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    page_students, total = _filter_and_page(all_students, query, page)
    await callback.message.edit_text(
        f"<b>Страница {page + 1}:</b>",
        reply_markup=kb_student_paged(page_students, page, total, query),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("student_card:"))
async def cb_student_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, teacher_repo: TeacherRepository, ts_repo: TeacherStudentRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    teacher_ids = await ts_repo.get_teachers_for_student(student_id)
    if teacher_ids:
        all_teachers = await teacher_repo.get_all()
        teachers_map = {t.teacher_id: t.name for t in all_teachers}
        teachers_text = "\n".join(f"  • {teachers_map.get(tid, tid)}" for tid in teacher_ids)
    else:
        teachers_text = "  не привязан"

    if student.partner_id:
        partner = await student_repo.get_by_id(student.partner_id)
        partner_text = partner.name if partner else f"(удалён: {student.partner_id})"
    else:
        partner_text = "— (солист)"

    if student.group_id:
        group = await group_repo.get_by_id(student.group_id)
        if group:
            branch = await branch_repo.get_by_id(group.branch_id)
            branch_name = branch.name if branch else group.branch_id
            group_text = f"<b>{group.name}</b> ({branch_name})"
        else:
            group_text = f"(не найдена: {student.group_id})"
    else:
        group_text = "<i>не задана</i>"

    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n"
        f"🏢 Группа: {group_text}\n\n"
        f"Педагоги:\n{teachers_text}\n\n"
        f"Партнёр: {partner_text}"
    )
    await callback.message.edit_text(
        text, reply_markup=kb_student_card(student_id, has_partner=bool(student.partner_id))
    )
    await callback.answer()


# ─── Добавление ученика ───────────────────────────────────────────────────────

@router.callback_query(F.data == "students:add")
async def cb_add_student_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddStudentStates.entering_name)
    await callback.message.edit_text("<b>Добавление ученика</b>\nВведите Фамилию Имя ученика:")
    await callback.answer()


@router.message(AddStudentStates.entering_name)
async def add_student_name(
    message: Message, state: FSMContext, branch_repo: BranchRepository,
) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Фамилия Имя не может быть пустым. Введите ещё раз:")
        return
    if len(name.split()) < 2:
        await message.answer(
            "Нужно указать и фамилию, и имя (например: <b>Иванова Мария</b>). Введите ещё раз:"
        )
        return
    await state.update_data(name=name)
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    if not branches:
        await state.clear()
        await message.answer(
            "Нет ни одного филиала. Создайте филиал и группу в «🏢 Филиалы и группы».",
            reply_markup=kb_back("admin:students"),
        )
        return
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"add_st_branch:{b.branch_id}")]
        for b in branches
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="admin:students")])
    await state.set_state(AddStudentStates.choosing_branch)
    await message.answer(
        f"<b>Новый ученик: {name}</b>\nВыберите филиал:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("add_st_branch:"), AddStudentStates.choosing_branch)
async def cb_add_student_branch(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    branch_id = callback.data.split(":", 1)[1]
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: g.name)
    if not groups:
        await callback.answer(
            "В этом филиале нет групп. Создайте группу в «🏢 Филиалы и группы».",
            show_alert=True,
        )
        return
    await state.update_data(branch_id=branch_id)
    await state.set_state(AddStudentStates.choosing_group)
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"add_st_group:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    await callback.message.edit_text(
        "<b>Выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_st_group:"), AddStudentStates.choosing_group)
async def cb_add_student_group(
    callback: CallbackQuery, state: FSMContext, user: User | None,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    await state.update_data(group_id=group_id)
    data = await state.get_data()
    await state.set_state(AddStudentStates.confirming)
    await callback.message.edit_text(
        f"<b>Добавить ученика «{data['name']}»?</b>\n(группа будет назначена)",
        reply_markup=kb_confirm("confirm_add_student", "admin:students"),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_add_student")
async def cb_confirm_add_student(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    toast = ""
    try:
        student = await student_repo.add(data["name"])
        group_id = data.get("group_id") or ""
        if group_id:
            await student_repo.update_group(student.student_id, group_id)
            group = await group_repo.get_by_id(group_id)
            if group:
                branch = await branch_repo.get_by_id(group.branch_id)
                bname = branch.name if branch else "—"
                toast = f"Ученик «{student.name}» добавлен в группу «{group.name}» (филиал «{bname}»)"
        if not toast:
            toast = f"Ученик «{student.name}» добавлен"
        await callback.message.edit_text(
            f"<b>Ученик добавлен!</b>\nID: {student.student_id}\nФамилия Имя: {student.name}",
            reply_markup=kb_back("admin:students"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления ученика: %s", exc)
        await callback.message.edit_text("Ошибка при добавлении ученика.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await callback.answer(toast, show_alert=True)


# ─── Удаление ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "students:delete")
async def cb_delete_student_start(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    students = await student_repo.get_all()
    if not students:
        await callback.message.edit_text("Учеников нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите ученика для удаления:</b>", reply_markup=kb_student_list(students, "del_student")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_student:"))
async def cb_delete_student_confirm(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить ученика «{student.name}» ({student_id})?</b>",
        reply_markup=kb_confirm(f"confirm_del_student:{student_id}", "admin:students"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_student:"))
async def cb_delete_student_do(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    for ts in await ts_repo.get_all():
        if ts.student_id == student_id:
            await ts_repo.remove(ts.teacher_id, student_id)
    ok = await student_repo.delete(student_id)
    text = f"Ученик {student_id} удалён (и все его связи с педагогами)." if ok else "Ученик не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:students"))
    await callback.answer()



# ─── Управление партнёром ученика ────────────────────────────────────────────

@router.callback_query(F.data.startswith("partner_assign:"))
async def cb_partner_assign_start(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    student_repo: StudentRepository,
    ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    # Кандидаты — те, у кого есть хотя бы один общий педагог с текущим учеником.
    s_teachers = set(await ts_repo.get_teachers_for_student(student_id))
    if not s_teachers:
        await callback.message.edit_text(
            "У ученика нет привязанных педагогов — сначала привяжите хотя бы одного.",
            reply_markup=kb_back(f"student_card:{student_id}"),
        )
        await callback.answer()
        return

    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    candidates: list = []
    for other in all_students:
        if other.student_id == student_id:
            continue
        other_teachers = set(await ts_repo.get_teachers_for_student(other.student_id))
        if not (s_teachers & other_teachers):
            continue
        # Уже текущий партнёр того же ученика — пропускаем (назначать не на что).
        if other.student_id == student.partner_id:
            continue
        has_partner = bool(other.partner_id)
        candidates.append((other, has_partner))

    if not candidates:
        await callback.message.edit_text(
            "Нет подходящих кандидатов (нужен общий педагог).",
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

    await state.update_data(partner_id=partner_id)
    await state.set_state(PartnerAssignStates.confirming)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_confirm("confirm_partner", f"student_card:{student_id}"),
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
    try:
        await student_repo.set_partner(student_id, partner_id)
        await callback.message.edit_text(
            "Партнёры назначены.", reply_markup=kb_back(f"student_card:{student_id}")
        )
    except ValueError as exc:
        await callback.message.edit_text(
            f"Ошибка: {exc}", reply_markup=kb_back(f"student_card:{student_id}")
        )
    except Exception as exc:
        logger.error("Ошибка назначения партнёра: %s", exc)
        await callback.message.edit_text(
            "Не удалось назначить партнёра. Попробуйте позже.",
            reply_markup=kb_back(f"student_card:{student_id}"),
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
            f"confirm_partner_clear:{student_id}", f"student_card:{student_id}"
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


# ─── Заявки педагогов на создание новых учеников ─────────────────────────────

def _find_similar_students(all_students: list, name: str) -> list:
    """Substring-match по токенам имени (длина ≥ 3). Возвращает до 10 похожих."""
    tokens = [t.lower() for t in name.split() if len(t) >= 3]
    if not tokens:
        return []
    similar = []
    for s in all_students:
        sn = s.name.lower()
        if any(t in sn for t in tokens):
            similar.append(s)
    return similar[:10]


async def _notify_other_admins(bot, req: dict, except_chat_id: int, resolution_text: str) -> None:
    """Редактирует сообщения у остальных админов, показывая что заявка уже обработана."""
    for chat_id, message_id in req.get("admin_msgs", []):
        if chat_id == except_chat_id:
            continue
        try:
            await bot.edit_message_text(
                resolution_text, chat_id=chat_id, message_id=message_id,
            )
        except Exception:
            pass


async def _finalize_link(
    bot, req: dict, student_id: str, student_name: str,
    ts_repo: TeacherStudentRepository,
) -> str:
    """Создаёт связь педагог↔ученик, уведомляет педагога. Возвращает текст для админа."""
    teacher_id = req["teacher_id"]
    if await ts_repo.exists(teacher_id, student_id):
        link_note = "Связь уже существовала."
    else:
        await ts_repo.add(teacher_id, student_id)
        link_note = "Ученик привязан к педагогу."
    try:
        await bot.send_message(
            req["teacher_tg_id"],
            f"✅ Ученик <b>{student_name}</b> добавлен в ваш список.",
        )
    except Exception as exc:
        logger.error("Не удалось уведомить педагога о создании ученика: %s", exc)
    return link_note


async def _group_toast(
    student_name: str, group_id: str,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> str:
    if not group_id:
        return f"Ученик «{student_name}» создан"
    group = await group_repo.get_by_id(group_id)
    if not group:
        return f"Ученик «{student_name}» создан"
    branch = await branch_repo.get_by_id(group.branch_id)
    bname = branch.name if branch else "—"
    return f"Ученик «{student_name}» добавлен в группу «{group.name}» (филиал «{bname}»)"


@router.callback_query(F.data.startswith("req_approve:"))
async def cb_approve_student_request(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
    student_requests: dict,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = student_requests.get(req_id)
    if not req:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    name = req["student_name"]
    all_students = await student_repo.get_all()
    similar = _find_similar_students(all_students, name)
    if similar:
        rows = [
            [InlineKeyboardButton(
                text=f"🔗 Привязать: {s.name}",
                callback_data=f"req_link_existing:{req_id}:{s.student_id}",
            )] for s in similar
        ]
        rows.append([InlineKeyboardButton(
            text="➕ Всё равно создать нового", callback_data=f"req_create_new:{req_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="❌ Отменить", callback_data=f"req_reject:{req_id}",
        )])
        await callback.message.edit_text(
            f"📝 Заявка от <b>{req['teacher_name']}</b> на ученика <b>{name}</b>.\n\n"
            f"⚠️ Найдены похожие ученики в базе:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return
    # Дублей нет — создаём сразу
    student = await student_repo.add(name=name)
    if req.get("group_id"):
        await student_repo.update_group(student.student_id, req["group_id"])
    link_note = await _finalize_link(callback.bot, req, student.student_id, student.name, ts_repo)
    student_requests.pop(req_id, None)
    await callback.message.edit_text(
        f"✅ Ученик <b>{student.name}</b> создан (ID: {student.student_id}).\n{link_note}",
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"✅ Заявка обработана админом @{callback.from_user.username or callback.from_user.id}",
    )
    toast = await _group_toast(student.name, req.get("group_id") or "", group_repo, branch_repo)
    await callback.answer(toast, show_alert=True)


@router.callback_query(F.data.startswith("req_create_new:"))
async def cb_create_new_student_request(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
    student_requests: dict,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = student_requests.get(req_id)
    if not req:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    student = await student_repo.add(name=req["student_name"])
    if req.get("group_id"):
        await student_repo.update_group(student.student_id, req["group_id"])
    link_note = await _finalize_link(callback.bot, req, student.student_id, student.name, ts_repo)
    student_requests.pop(req_id, None)
    await callback.message.edit_text(
        f"✅ Ученик <b>{student.name}</b> создан (ID: {student.student_id}).\n{link_note}",
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"✅ Заявка обработана админом @{callback.from_user.username or callback.from_user.id}",
    )
    toast = await _group_toast(student.name, req.get("group_id") or "", group_repo, branch_repo)
    await callback.answer(toast, show_alert=True)


@router.callback_query(F.data.startswith("req_link_existing:"))
async def cb_link_existing_student_request(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, ts_repo: TeacherStudentRepository,
    student_requests: dict,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, req_id, student_id = callback.data.split(":", 2)
    req = student_requests.get(req_id)
    if not req:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден.", show_alert=True)
        return
    link_note = await _finalize_link(callback.bot, req, student.student_id, student.name, ts_repo)
    student_requests.pop(req_id, None)
    await callback.message.edit_text(
        f"🔗 Ученик <b>{student.name}</b> (ID: {student.student_id}) привязан к педагогу.\n{link_note}",
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"✅ Заявка обработана админом @{callback.from_user.username or callback.from_user.id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("req_reject:"))
async def cb_reject_student_request(
    callback: CallbackQuery, user: User | None, student_requests: dict,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = student_requests.pop(req_id, None)
    if not req:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    try:
        await callback.bot.send_message(
            req["teacher_tg_id"],
            f"❌ Заявка на создание ученика <b>{req['student_name']}</b> отклонена.\n"
            "Свяжитесь с администратором лично.",
        )
    except Exception as exc:
        logger.error("Не удалось уведомить педагога об отклонении заявки: %s", exc)
    await callback.message.edit_text(
        f"❌ Заявка от <b>{req['teacher_name']}</b> на <b>{req['student_name']}</b> отклонена.",
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"❌ Заявка отклонена админом @{callback.from_user.username or callback.from_user.id}",
    )
    await callback.answer()
