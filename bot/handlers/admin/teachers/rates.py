from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.models import User

from bot.repositories import (
    TeacherRepository,
)
from bot.states import EditTeacherRatesStates
from bot.keyboards.admin import kb_confirm, kb_back




from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Изменение ставок ─────────────────────────────────────────────────────────

_RATE_LABELS = {
    "group":   "Групповое",
    "teacher": "Инд. педагогу",
    "student": "Инд. ученику",
}


@router.callback_query(F.data.startswith("edit_rate:"), EditTeacherRatesStates.choosing_rate)
async def cb_edit_rate_pick(callback: CallbackQuery, state: FSMContext) -> None:
    _, rate_type, teacher_id = callback.data.split(":", 2)
    await state.update_data(rate_type=rate_type)
    label = _RATE_LABELS.get(rate_type, rate_type)
    await state.set_state(EditTeacherRatesStates.entering_rate)
    await callback.message.edit_text(
        f"<b>Новая ставка «{label}» (руб. за 45 мин):</b>",
        reply_markup=kb_back(f"card_edit_rates:{teacher_id}"),
    )
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
        reply_markup=kb_confirm("confirm_edit_rates", f"card_edit_rates:{data['teacher_id']}"),
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
    teacher_id = data["teacher_id"]
    ok = await teacher_repo.update_rates(
        teacher_id, data["rate_group"], data["rate_for_teacher"], data["rate_for_student"]
    )
    text = "Ставка обновлена." if ok else "Педагог не найден."
    await callback.message.edit_text(text, reply_markup=kb_back(f"teacher_card:{teacher_id}"))
    await callback.answer()
