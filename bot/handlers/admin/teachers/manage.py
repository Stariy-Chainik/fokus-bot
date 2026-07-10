from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.models import User

from bot.repositories import (
    TeacherRepository,
    TeacherGroupRepository,
)
from bot.states import AddTeacherStates
from bot.keyboards.admin import kb_confirm, kb_back




from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


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
        "Узнать ID можно через @userinfobot — педагог отправляет ему /start.",
        reply_markup=kb_back("teachers:list"),
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
            reply_markup=kb_back("teachers:list"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления педагога: %s", exc)
        await message.answer("Ошибка при добавлении педагога.", reply_markup=kb_back("teachers:list"))


# ─── Удаление педагога ────────────────────────────────────────────────────────

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
        reply_markup=kb_confirm(
            f"confirm_del_teacher:{teacher_id}", f"teacher_card:{teacher_id}",
            confirm_text="🗑 Удалить",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_teacher:"))
async def cb_delete_teacher_do(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, user_repo,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await teacher_group_repo.remove_all_for_teacher(teacher_id)
    ok = await teacher_repo.delete(teacher_id)
    if ok:
        await user_repo.delete_by_teacher_id(teacher_id)
    text = f"Педагог {teacher_id} удалён." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("teachers:list"))
    await callback.answer()

