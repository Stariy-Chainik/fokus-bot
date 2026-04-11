from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.models import User
from bot.repositories import TeacherRepository
from bot.states import AddTeacherStates, EditTeacherRatesStates
from bot.keyboards.admin import kb_teachers_menu, kb_teacher_list, kb_confirm, kb_back

logger = logging.getLogger(__name__)
router = Router(name="admin_teachers")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


@router.callback_query(F.data == "admin:teachers")
async def cb_teachers_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Управление педагогами:", reply_markup=kb_teachers_menu())
    await callback.answer()


# ─── Добавление педагога ──────────────────────────────────────────────────────

@router.callback_query(F.data == "teachers:add")
async def cb_add_teacher_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddTeacherStates.entering_name)
    await callback.message.edit_text("Введите имя педагога:")
    await callback.answer()


@router.message(AddTeacherStates.entering_name)
async def add_teacher_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Имя не может быть пустым:")
        return
    await state.update_data(name=name)
    await state.set_state(AddTeacherStates.entering_tg_id)
    await message.answer("Введите Telegram ID педагога (число) или 0 если неизвестен:")


@router.message(AddTeacherStates.entering_tg_id)
async def add_teacher_tg_id(message: Message, state: FSMContext) -> None:
    try:
        tg_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите число или 0:")
        return
    await state.update_data(tg_id=tg_id if tg_id != 0 else None)
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
async def add_teacher_rate_student(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_for_student=rate)
    data = await state.get_data()
    await state.set_state(AddTeacherStates.confirming)
    await message.answer(
        f"Проверьте данные:\n\n"
        f"Имя: {data['name']}\n"
        f"Telegram ID: {data.get('tg_id') or 'не указан'}\n"
        f"Ставка групп: {data['rate_group']} руб.\n"
        f"Ставка педагогу (инд.): {data['rate_for_teacher']} руб.\n"
        f"Ставка ученику (инд.): {rate} руб.",
        reply_markup=kb_confirm("confirm_add_teacher", "admin:teachers"),
    )


@router.callback_query(F.data == "confirm_add_teacher")
async def cb_confirm_add_teacher(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    teacher_repo: TeacherRepository, user_repo,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
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
        # Автоматически привязываем teacher_id к пользователю в таблице users
        linked = False
        if teacher.tg_id:
            linked = await user_repo.update_teacher_id(teacher.tg_id, teacher.teacher_id)

        note = "\n✅ Аккаунт педагога привязан." if linked else (
            "\n⚠️ Telegram ID не найден в таблице users — привяжите teacher_id вручную." if teacher.tg_id else ""
        )
        await callback.message.edit_text(
            f"Педагог добавлен!\nID: {teacher.teacher_id}\nИмя: {teacher.name}{note}",
            reply_markup=kb_back("admin:teachers"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления педагога: %s", exc)
        await callback.message.edit_text("Ошибка при добавлении педагога.", reply_markup=kb_back("admin:teachers"))
    await callback.answer()


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
        "Выберите педагога для удаления:", reply_markup=kb_teacher_list(teachers, "del_teacher")
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
        f"Удалить педагога «{teacher.name}» ({teacher_id})?\nЗанятия останутся.",
        reply_markup=kb_confirm(f"confirm_del_teacher:{teacher_id}", "admin:teachers"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_teacher:"))
async def cb_delete_teacher_do(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository, user_repo,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    ok = await teacher_repo.delete(teacher_id)
    if ok:
        await user_repo.delete_by_teacher_id(teacher_id)
    text = f"Педагог {teacher_id} удалён." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:teachers"))
    await callback.answer()


# ─── Изменение ставок ─────────────────────────────────────────────────────────

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
        "Выберите педагога:", reply_markup=kb_teacher_list(teachers, "edit_rates_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_rates_teacher:"), EditTeacherRatesStates.choosing_teacher)
async def cb_edit_rates_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(teacher_id=callback.data.split(":", 1)[1])
    await state.set_state(EditTeacherRatesStates.entering_rate_group)
    await callback.message.edit_text("Новая ставка за групповое занятие (руб. за 45 мин):")
    await callback.answer()


@router.message(EditTeacherRatesStates.entering_rate_group)
async def edit_rate_group(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_group=rate)
    await state.set_state(EditTeacherRatesStates.entering_rate_for_teacher)
    await message.answer("Новая ставка педагогу за индивидуальное (руб. за 45 мин):")


@router.message(EditTeacherRatesStates.entering_rate_for_teacher)
async def edit_rate_teacher(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_for_teacher=rate)
    await state.set_state(EditTeacherRatesStates.entering_rate_for_student)
    await message.answer("Новая ставка для счёта ученика (руб. за 45 мин):")


@router.message(EditTeacherRatesStates.entering_rate_for_student)
async def edit_rate_student(message: Message, state: FSMContext) -> None:
    try:
        rate = int((message.text or "").strip())
        assert rate >= 0
    except (ValueError, AssertionError):
        await message.answer("Введите положительное целое число:")
        return
    await state.update_data(rate_for_student=rate)
    data = await state.get_data()
    await state.set_state(EditTeacherRatesStates.confirming)
    await message.answer(
        f"Новые ставки для {data['teacher_id']}:\n"
        f"Групп: {data['rate_group']} руб.\n"
        f"Педагогу (инд.): {data['rate_for_teacher']} руб.\n"
        f"Ученику (инд.): {rate} руб.",
        reply_markup=kb_confirm("confirm_edit_rates", "admin:teachers"),
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
    text = "Ставки обновлены." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:teachers"))
    await callback.answer()
