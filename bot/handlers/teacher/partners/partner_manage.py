from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository,
)
from bot.services import TeacherVisibilityService
from bot.states import PartnerAssignStates, TeacherRenameStudentStates
from bot.keyboards.teacher import (
    kb_t_partner_candidates, kb_t_confirm,
)
from bot.handlers.access import is_teacher as _is_teacher

from ._base import router

logger = logging.getLogger(__name__)


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


