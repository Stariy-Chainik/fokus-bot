from __future__ import annotations
"""Педагог: «Группы» — просмотр своих групп, управление составом."""
import logging
import uuid

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

from bot.models import User
from bot.repositories import (
    StudentRepository, GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, TeacherRepository, UserRepository, StudentRequestRepository,
)
from bot.states import TeacherGroupAddStudentStates

logger = logging.getLogger(__name__)


from bot.handlers.access import is_teacher as _is_teacher



from ._base import router, _owns_group, _normalize, _render_t_group_card


# ─── Добавить ученика (поиск или создать через заявку) ──────────────────────

def _kb_t_add_results(
    group_id: str, found: list, member_ids: set[str], query: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in found[:20]:
        mark = " ✓" if s.student_id in member_ids else ""
        rows.append([InlineKeyboardButton(
            text=f"{s.name}{mark}",
            callback_data=f"t_grp_pick:{group_id}:{s.student_id}",
        )])
    parts = (query or "").strip().split()
    if len(parts) >= 2:
        rows.append([InlineKeyboardButton(
            text=f"✨ Создать нового «{' '.join(parts)}» (заявка админу)",
            callback_data=f"t_grp_new:{group_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("t_grp_add:"))
async def cb_t_grp_add(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    group_repo: GroupRepository, teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    await state.set_state(TeacherGroupAddStudentStates.searching)
    await state.update_data(group_id=group_id)
    await callback.message.edit_text(
        f"<b>➕ Добавить ученика в «{group.name if group else group_id}»</b>\n\n"
        "Введите <b>Фамилию Имя</b> (или часть). Бот найдёт совпадения среди учеников школы.\n"
        "Если нужного ученика нет — можно создать нового (уйдёт как заявка админу).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")],
        ]),
    )
    await callback.answer()


@router.message(TeacherGroupAddStudentStates.searching)
async def msg_t_grp_add_search(
    message: Message, state: FSMContext, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
) -> None:
    if not _is_teacher(user):
        return
    data = await state.get_data()
    group_id = str(data.get("group_id") or "")
    if not group_id:
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("❗ Введите ФИО или часть.")
        return
    await state.update_data(query=query)

    q_norm = _normalize(query)
    all_students = await student_repo.get_all()
    found = sorted(
        [s for s in all_students if q_norm in s.name.lower()],
        key=lambda s: s.name,
    )
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    group = await group_repo.get_by_id(group_id)
    header = f"Группа: <b>{group.name if group else group_id}</b>"

    if not found:
        parts = query.split()
        lines = [header, "", f"По запросу «{query}» ничего не найдено."]
        if len(parts) == 2:
            lines.append("")
            lines.append("Можно создать нового ученика (заявка админу).")
        else:
            lines.append("")
            lines.append("Для создания нового введите <b>Фамилию и Имя</b> (можно с номером группы д/с).")
        await message.answer(
            "\n".join(lines),
            reply_markup=_kb_t_add_results(group_id, [], member_ids, query),
        )
        return

    lines = [header, "", f"Найдено: <b>{len(found)}</b>. ✓ — уже в этой группе."]
    await message.answer(
        "\n".join(lines),
        reply_markup=_kb_t_add_results(group_id, found, member_ids, query),
    )


@router.callback_query(F.data.startswith("t_grp_pick:"), TeacherGroupAddStudentStates.searching)
async def cb_t_grp_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_group_repo: StudentGroupRepository,
    student_repo: StudentRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    if await student_group_repo.exists(student_id, group_id):
        await callback.answer(f"{student.name} уже в этой группе", show_alert=True)
    else:
        await student_group_repo.add(student_id, group_id)
        await callback.answer(f"✅ {student.name} добавлен(а)")
    await state.clear()
    await _render_t_group_card(
        callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
    )


@router.callback_query(F.data.startswith("t_grp_new:"), TeacherGroupAddStudentStates.searching)
async def cb_t_grp_new(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository, user_repo: UserRepository,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    data = await state.get_data()
    query = str(data.get("query") or "").strip()
    parts = query.split()
    if len(parts) < 2:
        await callback.answer("Нужно минимум Фамилию и Имя", show_alert=True)
        return
    student_name = " ".join(parts)

    # Shortcut: педагог-админ создаёт напрямую.
    if user.is_admin:
        student = await student_repo.add(name=student_name)
        await student_group_repo.add(student.student_id, group_id)
        await state.clear()
        await callback.answer(f"✅ Создан: {student.name}")
        await _render_t_group_card(
            callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
        )
        return

    # Заявка админу.
    teacher = await teacher_repo.get_by_id(user.teacher_id)
    teacher_name = teacher.name if teacher else user.teacher_id
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    if not admins:
        await callback.answer("В системе нет администратора.", show_alert=True)
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
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа о заявке tg_id=%s: %s", admin.tg_id, exc)

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
            [InlineKeyboardButton(text="« Назад к группе", callback_data=f"t_group_card:{group_id}")],
            [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
        ]),
    )
    await callback.answer()


# ─── Убрать ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("t_grp_rm:"))
async def cb_t_grp_rm(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not members:
        await callback.answer("В группе нет учеников", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=f"➖ {s.name}", callback_data=f"t_grp_rm_do:{group_id}:{s.student_id}")]
        for s in members
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"t_group_card:{group_id}")])
    await callback.message.edit_text(
        f"<b>➖ Убрать из «{group.name}»</b>\n\nВыберите ученика — он будет убран только из этой группы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_grp_rm_do:"))
async def cb_t_grp_rm_do(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, student_id = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    removed = await student_group_repo.remove(student_id, group_id)
    if removed and student:
        await callback.answer(f"✅ {student.name} убран(а) из группы")
    else:
        await callback.answer("Не удалось убрать")
    await _render_t_group_card(
        callback.message, group_id, group_repo, branch_repo, student_repo, student_group_repo,
    )


