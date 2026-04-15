from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    TeacherRepository, TeacherStudentRepository, UserRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
)
from bot.states import AddTeacherStates, EditTeacherRatesStates
from bot.keyboards.admin import kb_teachers_menu, kb_teacher_list, kb_teacher_card, kb_rate_select, kb_confirm, kb_back

logger = logging.getLogger(__name__)
router = Router(name="admin_teachers")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


@router.callback_query(F.data == "admin:teachers")
async def cb_teachers_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("<b>Управление педагогами:</b>", reply_markup=kb_teachers_menu())
    await callback.answer()


# ─── Список педагогов ────────────────────────────────────────────────────────

@router.callback_query(F.data == "teachers:list")
async def cb_teachers_list(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:teachers"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите педагога:</b>",
        reply_markup=kb_teacher_list(teachers, "teacher_card", back_cb="admin:teachers"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teacher_card:"))
async def cb_teacher_card(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    tg_info = f"@tg_id: {teacher.tg_id}" if teacher.tg_id else "Telegram не привязан"

    gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    groups = [g for g in await group_repo.get_all() if g.group_id in gids]
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups.sort(key=lambda g: (branches.get(g.branch_id, ""), g.name))
    if groups:
        groups_block = "\n".join(
            f"  • {branches.get(g.branch_id, '—')} / {g.name}" for g in groups
        )
    else:
        groups_block = "  —"

    text = (
        f"👨‍🏫 <b>{teacher.name}</b>\n"
        f"ID: {teacher.teacher_id}\n"
        f"{tg_info}\n\n"
        f"📊 Ставки (руб. за 45 мин):\n"
        f"  Групповое: <b>{teacher.rate_group}</b>\n"
        f"  Инд. педагогу: <b>{teacher.rate_for_teacher}</b>\n"
        f"  Инд. ученику: <b>{teacher.rate_for_student}</b>\n\n"
        f"🏢 Группы:\n{groups_block}"
    )
    await callback.message.edit_text(text, reply_markup=kb_teacher_card(teacher_id))
    await callback.answer()


def _kb_teacher_groups_edit(teacher_id: str, groups: list, branches: dict, assigned: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for g in groups:
        mark = "✅ " if g.group_id in assigned else "☐ "
        bname = branches.get(g.branch_id, "—")
        rows.append([InlineKeyboardButton(
            text=f"{mark}{bname} / {g.name}",
            callback_data=f"teg_toggle:{teacher_id}:{g.group_id}",
        )])
    rows.append([InlineKeyboardButton(text="💾 Готово", callback_data=f"teacher_card:{teacher_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("t_edit_groups:"))
async def cb_t_edit_groups(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups = sorted(
        await group_repo.get_all(),
        key=lambda g: (branches.get(g.branch_id, ""), g.name),
    )
    assigned = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    await callback.message.edit_text(
        f"<b>Группы педагога «{teacher.name}»</b>\n"
        "✅ — назначен(а). Тап переключает.",
        reply_markup=_kb_teacher_groups_edit(teacher_id, groups, branches, assigned),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teg_toggle:"))
async def cb_teg_toggle(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, group_id = callback.data.split(":", 2)
    assigned = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    if group_id in assigned:
        await teacher_group_repo.remove(teacher_id, group_id)
    else:
        await teacher_group_repo.add(teacher_id, group_id)
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups = sorted(
        await group_repo.get_all(),
        key=lambda g: (branches.get(g.branch_id, ""), g.name),
    )
    assigned = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    await callback.message.edit_reply_markup(
        reply_markup=_kb_teacher_groups_edit(teacher_id, groups, branches, assigned),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("card_edit_rates:"))
async def cb_card_edit_rates(
    callback: CallbackQuery, user: User | None, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    await state.update_data(
        teacher_id=teacher_id,
        rate_group=teacher.rate_group,
        rate_for_teacher=teacher.rate_for_teacher,
        rate_for_student=teacher.rate_for_student,
    )
    await state.set_state(EditTeacherRatesStates.choosing_rate)
    await callback.message.edit_text(
        f"Педагог: <b>{teacher.name}</b>\n\nКакую ставку изменить?",
        reply_markup=kb_rate_select(
            teacher_id, teacher.rate_group, teacher.rate_for_teacher, teacher.rate_for_student
        ),
    )
    await callback.answer()


# ─── Добавление педагога ──────────────────────────────────────────────────────

@router.callback_query(F.data == "teachers:add")
async def cb_add_teacher_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddTeacherStates.entering_tg_id)
    await callback.message.edit_text(
        "<b>Добавление педагога</b>\n"
        "Введите Telegram ID педагога (число).\n"
        "Узнать ID можно через @userinfobot — педагог отправляет ему /start."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_teacher_prefill:"))
async def cb_add_teacher_prefill(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        tg_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await state.update_data(tg_id=tg_id)
    await state.set_state(AddTeacherStates.entering_name)
    await callback.message.answer(
        f"<b>Добавление педагога</b>\nTelegram ID: <code>{tg_id}</code>\n\nВведите Фамилию Имя педагога:"
    )
    await callback.answer()


@router.message(AddTeacherStates.entering_tg_id)
async def add_teacher_tg_id(message: Message, state: FSMContext) -> None:
    try:
        tg_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите корректный Telegram ID (число):")
        return
    if tg_id <= 0:
        await message.answer("Telegram ID должен быть положительным числом. Попробуйте ещё раз:")
        return
    await state.update_data(tg_id=tg_id)
    await state.set_state(AddTeacherStates.entering_name)
    await message.answer("Введите Фамилию Имя педагога:")


@router.message(AddTeacherStates.entering_name)
async def add_teacher_name(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not name:
        await message.answer("Фамилия Имя не может быть пустым. Введите ещё раз:")
        return
    if len(name.split()) < 2:
        await message.answer(
            "Нужно указать и фамилию, и имя (например: <b>Петрова Екатерина</b>). Введите ещё раз:"
        )
        return
    await state.update_data(name=name)
    await state.set_state(AddTeacherStates.entering_rate_group)
    await message.answer("Ставка за групповое занятие (руб. за 45 мин):")


@router.message(AddTeacherStates.entering_rate_group)
async def add_teacher_rate_group(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_group=rate)
    await state.set_state(AddTeacherStates.entering_rate_for_teacher)
    await message.answer("Ставка за индивидуальное занятие — педагогу (руб. за 45 мин):")


@router.message(AddTeacherStates.entering_rate_for_teacher)
async def add_teacher_rate_teacher(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_for_teacher=rate)
    await state.set_state(AddTeacherStates.entering_rate_for_student)
    await message.answer("Ставка за индивидуальное — для счёта ученика (руб. за 45 мин):")


@router.message(AddTeacherStates.entering_rate_for_student)
async def add_teacher_rate_student(
    message: Message, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, user_repo,
) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    if not _is_admin(user):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    await state.update_data(rate_for_student=rate)
    data = await state.get_data()
    await state.clear()
    try:
        teacher = await teacher_repo.add(
            tg_id=data.get("tg_id"),
            name=data["name"],
            rate_group=data["rate_group"],
            rate_for_teacher=data["rate_for_teacher"],
            rate_for_student=data["rate_for_student"],
        )
        note = ""
        if teacher.tg_id:
            existing = await user_repo.get_by_tg_id(teacher.tg_id)
            if existing is None:
                await user_repo.add(tg_id=teacher.tg_id, teacher_id=teacher.teacher_id)
                note = "\n✅ Аккаунт педагога создан и привязан."
            else:
                await user_repo.update_teacher_id(teacher.tg_id, teacher.teacher_id)
                note = "\n✅ Аккаунт педагога привязан."
        await message.answer(
            f"<b>Педагог добавлен!</b>\nID: {teacher.teacher_id}\nФамилия Имя: {teacher.name}{note}",
            reply_markup=kb_back("admin:teachers"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления педагога: %s", exc)
        await message.answer("Ошибка при добавлении педагога.", reply_markup=kb_back("admin:teachers"))


# ─── Удаление педагога ────────────────────────────────────────────────────────

@router.callback_query(F.data == "teachers:delete")
async def cb_delete_teacher_start(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:teachers"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите педагога для удаления:</b>", reply_markup=kb_teacher_list(teachers, "del_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_teacher:"))
async def cb_delete_teacher_confirm(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Удалить педагога «{teacher.name}» ({teacher_id})?</b>\nЗанятия останутся.",
        reply_markup=kb_confirm(f"confirm_del_teacher:{teacher_id}", "admin:teachers"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_teacher:"))
async def cb_delete_teacher_do(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, user_repo,
    ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    # Удаляем связи teacher_students (аналогично удалению ученика)
    for ts in await ts_repo.get_all():
        if ts.teacher_id == teacher_id:
            await ts_repo.remove(teacher_id, ts.student_id)
    ok = await teacher_repo.delete(teacher_id)
    if ok:
        await user_repo.delete_by_teacher_id(teacher_id)
    text = f"Педагог {teacher_id} удалён." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:teachers"))
    await callback.answer()


# ─── Изменение ставок ─────────────────────────────────────────────────────────

_RATE_LABELS = {
    "group":   "Групповое",
    "teacher": "Инд. педагогу",
    "student": "Инд. ученику",
}


@router.callback_query(F.data == "teachers:edit_rates")
async def cb_edit_rates_start(
    callback: CallbackQuery, user: User | None, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:teachers"))
        await callback.answer()
        return
    await state.set_state(EditTeacherRatesStates.choosing_teacher)
    await callback.message.edit_text(
        "<b>Выберите педагога:</b>", reply_markup=kb_teacher_list(teachers, "edit_rates_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_rates_teacher:"), EditTeacherRatesStates.choosing_teacher)
async def cb_edit_rates_chosen(
    callback: CallbackQuery, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    await state.update_data(
        teacher_id=teacher_id,
        rate_group=teacher.rate_group,
        rate_for_teacher=teacher.rate_for_teacher,
        rate_for_student=teacher.rate_for_student,
    )
    await state.set_state(EditTeacherRatesStates.choosing_rate)
    await callback.message.edit_text(
        f"Педагог: <b>{teacher.name}</b>\n\nКакую ставку изменить?",
        reply_markup=kb_rate_select(
            teacher_id, teacher.rate_group, teacher.rate_for_teacher, teacher.rate_for_student
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_rate:"), EditTeacherRatesStates.choosing_rate)
async def cb_edit_rate_pick(callback: CallbackQuery, state: FSMContext) -> None:
    _, rate_type, teacher_id = callback.data.split(":", 2)
    await state.update_data(rate_type=rate_type)
    label = _RATE_LABELS.get(rate_type, rate_type)
    await state.set_state(EditTeacherRatesStates.entering_rate)
    await callback.message.edit_text(f"<b>Новая ставка «{label}» (руб. за 45 мин):</b>")
    await callback.answer()


@router.message(EditTeacherRatesStates.entering_rate)
async def edit_rate_value(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    data = await state.get_data()
    rate_type = data["rate_type"]
    updated = {
        "rate_group": data["rate_group"],
        "rate_for_teacher": data["rate_for_teacher"],
        "rate_for_student": data["rate_for_student"],
    }
    if rate_type == "group":
        updated["rate_group"] = rate
    elif rate_type == "teacher":
        updated["rate_for_teacher"] = rate
    elif rate_type == "student":
        updated["rate_for_student"] = rate
    await state.update_data(**updated)
    await state.set_state(EditTeacherRatesStates.confirming)
    label = _RATE_LABELS.get(rate_type, rate_type)
    await message.answer(
        f"<b>Изменить ставку «{label}» → {rate} руб.?</b>",
        reply_markup=kb_confirm("confirm_edit_rates", f"edit_rates_teacher:{data['teacher_id']}"),
    )


@router.callback_query(F.data == "confirm_edit_rates")
async def cb_confirm_edit_rates(
    callback: CallbackQuery, state: FSMContext, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    ok = await teacher_repo.update_rates(
        data["teacher_id"], data["rate_group"], data["rate_for_teacher"], data["rate_for_student"]
    )
    text = "Ставка обновлена." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:teachers"))
    await callback.answer()
