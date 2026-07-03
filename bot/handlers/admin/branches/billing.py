from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from datetime import date

from bot.repositories import (
    BranchRepository, GroupRepository, TeacherGroupRepository,
    TeacherRepository, StudentRepository, StudentGroupRepository,
)
from bot.services import PaymentService
from bot.states import (
    AddBranchStates, EditBranchNameStates,
    AddGroupStates, EditGroupNameStates,
    GroupBillingStates, GroupAddStudentStates,
)
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period
from bot.utils.locks import InProgressGuard
from bot.handlers.common import show_card
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Биллинг ученикам (per-visit) ────────────────────────────────────────────

_MODE_TITLES = {
    GroupBillingMode.NONE: "не выставляется (группа бесплатная или абонемент)",
    GroupBillingMode.PER_VISIT: "по посещениям (per-visit)",
    GroupBillingMode.SUBSCRIPTION: "абонемент (зарезервировано)",
}


def _kb_group_billing(group_id: str, mode: GroupBillingMode) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if mode == GroupBillingMode.PER_VISIT:
        rows.append([InlineKeyboardButton(
            text="✏️ Изменить тарифы", callback_data=f"group_billing_edit:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="🚫 Отключить биллинг", callback_data=f"group_billing_off:{group_id}",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="💰 Включить биллинг по посещениям",
            callback_data=f"group_billing_edit:{group_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_card:{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _billing_text(group_name: str, mode: GroupBillingMode,
                  price_short: int, duration_short: int,
                  price_full: int, duration_full: int) -> str:
    lines = [
        f"💰 <b>Биллинг группы «{group_name}»</b>",
        "",
        f"Режим: {_MODE_TITLES.get(mode, mode.value)}",
    ]
    if mode == GroupBillingMode.PER_VISIT:
        lines += [
            "",
            f"🕐 Короткий тариф: {duration_short} мин — {price_short}₽",
            f"🕐 Полный тариф: {duration_full} мин — {price_full}₽",
            "",
            "Тариф ученика выбирается в его карточке.",
            "На занятии у педагога появятся отметки длительности.",
        ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("group_billing:"))
async def cb_group_billing(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        _billing_text(
            group.name, group.billing_mode,
            group.price_short, group.duration_short,
            group.price_full, group.duration_full,
        ),
        reply_markup=_kb_group_billing(group_id, group.billing_mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group_billing_off:"))
async def cb_group_billing_off(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await group_repo.update_billing(
        group_id, GroupBillingMode.NONE,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    )
    await callback.message.edit_text(
        _billing_text(
            group.name, GroupBillingMode.NONE,
            group.price_short, group.duration_short,
            group.price_full, group.duration_full,
        ),
        reply_markup=_kb_group_billing(group_id, GroupBillingMode.NONE),
    )
    await callback.answer("Биллинг отключён")


@router.callback_query(F.data.startswith("group_billing_edit:"))
async def cb_group_billing_edit(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    await state.set_state(GroupBillingStates.entering_duration_short)
    await state.update_data(group_id=group_id)
    await callback.message.edit_text(
        "<b>Настройка тарифов (шаг 1/4)</b>\n\n"
        f"Текущий короткий тариф: {group.duration_short} мин.\n"
        "Введите длительность короткого тарифа в минутах (например, 35):",
        reply_markup=kb_back(f"group_billing:{group_id}"),
    )
    await callback.answer()


def _parse_positive_int(raw: str) -> int | None:
    raw = (raw or "").strip().replace(" ", "")
    if not raw.isdigit():
        return None
    v = int(raw)
    return v if v > 0 else None


@router.message(GroupBillingStates.entering_duration_short)
async def group_billing_duration_short(message: Message, state: FSMContext) -> None:
    v = _parse_positive_int(message.text or "")
    if v is None:
        await message.answer("Нужно целое число минут больше 0. Введите ещё раз:")
        return
    await state.update_data(duration_short=v)
    await state.set_state(GroupBillingStates.entering_price_short)
    await message.answer(
        f"<b>Шаг 2/4</b>\n\n"
        f"Короткий тариф: {v} мин.\n"
        "Введите стоимость одного посещения короткого тарифа в рублях (например, 500):",
    )


@router.message(GroupBillingStates.entering_price_short)
async def group_billing_price_short(message: Message, state: FSMContext) -> None:
    v = _parse_positive_int(message.text or "")
    if v is None:
        await message.answer("Нужно целое число рублей больше 0. Введите ещё раз:")
        return
    await state.update_data(price_short=v)
    await state.set_state(GroupBillingStates.entering_duration_full)
    await message.answer(
        f"<b>Шаг 3/4</b>\n\n"
        "Введите длительность полного тарифа в минутах (например, 60):",
    )


@router.message(GroupBillingStates.entering_duration_full)
async def group_billing_duration_full(message: Message, state: FSMContext) -> None:
    v = _parse_positive_int(message.text or "")
    if v is None:
        await message.answer("Нужно целое число минут больше 0. Введите ещё раз:")
        return
    await state.update_data(duration_full=v)
    await state.set_state(GroupBillingStates.entering_price_full)
    await message.answer(
        f"<b>Шаг 4/4</b>\n\n"
        f"Полный тариф: {v} мин.\n"
        "Введите стоимость одного посещения полного тарифа в рублях (например, 850):",
    )


@router.message(GroupBillingStates.entering_price_full)
async def group_billing_price_full(
    message: Message, state: FSMContext, group_repo: GroupRepository,
) -> None:
    v = _parse_positive_int(message.text or "")
    if v is None:
        await message.answer("Нужно целое число рублей больше 0. Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    group_id = data["group_id"]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await message.answer("Группа не найдена.", reply_markup=kb_back("admin:branches"))
        return
    duration_short = int(data["duration_short"])
    price_short = int(data["price_short"])
    duration_full = int(data["duration_full"])
    price_full = v
    await group_repo.update_billing(
        group_id, GroupBillingMode.PER_VISIT,
        price_short, duration_short, price_full, duration_full,
    )
    await message.answer(
        _billing_text(
            group.name, GroupBillingMode.PER_VISIT,
            price_short, duration_short, price_full, duration_full,
        ),
        reply_markup=_kb_group_billing(group_id, GroupBillingMode.PER_VISIT),
    )


