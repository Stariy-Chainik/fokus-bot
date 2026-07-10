"""Биллинг группы: абонемент (цена/мес, «только вперёд») и переопределения цены."""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from bot.repositories import (
    GroupRepository, StudentRepository, StudentGroupRepository,
    SubscriptionOverrideRepository,
)
from bot.services import PaymentService
from bot.states import GroupBillingStates
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period
from bot.handlers.access import is_admin as _is_admin

from ._base import router
from .billing_shared import (
    _billing_view, _override_periods, _parse_positive_int, _parse_non_negative_int,
)

logger = logging.getLogger(__name__)


# ─── Абонемент (SUBSCRIPTION): фикс-сумма в месяц ────────────────────────────

@router.callback_query(F.data.startswith("group_billing_sub:"))
async def cb_group_billing_sub(
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
    await state.set_state(GroupBillingStates.entering_sub_price)
    await state.update_data(group_id=group_id)
    current = (
        f"Текущая цена: {group.price_full} ₽/мес.\n"
        if group.billing_mode == GroupBillingMode.SUBSCRIPTION else ""
    )
    await callback.message.edit_text(
        "<b>💳 Абонемент группы</b>\n\n"
        f"{current}"
        "Введите цену абонемента в рублях за месяц (например, 3000).\n"
        "Сумма фиксированная — количество занятий в месяце не влияет.",
        reply_markup=kb_back(f"group_billing:{group_id}"),
    )
    await callback.answer()


@router.message(GroupBillingStates.entering_sub_price)
async def group_billing_sub_price(
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
    # Цена действует ТОЛЬКО ВПЕРЁД: спрашиваем месяц; прошлые активные месяцы
    # будут зафиксированы старой ценой (или 0 при первом включении).
    rows = [
        [InlineKeyboardButton(
            text=display_period(p), callback_data=f"subeff:{group_id}:{p}:{v}",
        )]
        for p in _override_periods()
    ]
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data=f"group_billing:{group_id}")])
    await message.answer(
        f"<b>💳 Абонемент: {v} ₽/мес</b>\n\n"
        "С какого месяца действует новая цена?\n"
        "<i>Прошлые месяцы с занятиями будут автоматически зафиксированы "
        "по прежним условиям — цена меняется только вперёд.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("subeff:"))
async def cb_sub_effective(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    payment_service: PaymentService,
    subscription_override_repo: SubscriptionOverrideRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, period, price_str = callback.data.split(":", 3)
    new_price = int(price_str)
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    # Прошлое фиксируем: старой ценой при смене, нулём при первом включении.
    was_subscription = group.billing_mode == GroupBillingMode.SUBSCRIPTION
    pin_amount = group.price_full if was_subscription else 0
    pinned = await payment_service.pin_subscription_history(group_id, period, pin_amount)

    await group_repo.update_billing(
        group_id, GroupBillingMode.SUBSCRIPTION,
        group.price_short, group.duration_short, new_price, group.duration_full,
    )
    logger.info("Группа %s: абонемент %d ₽/мес с %s (зафиксировано мес.: %d)",
                group_id, new_price, period, pinned)

    group = await group_repo.get_by_id(group_id)  # перечитать с новой ценой
    note = f"\n\n✅ Новая цена действует с {display_period(period)}."
    if pinned:
        note += f"\nПрошлых месяцев зафиксировано: {pinned}."
    text, kb = await _billing_view(group, subscription_override_repo, student_repo, note=note)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ─── Переопределение цены абонемента на месяц (ученик / вся группа) ──────────

@router.callback_query(F.data.startswith("subovr:m:"))
async def cb_subovr_pick_month(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, group_id, scope = callback.data.split(":", 3)
    label = "всей группы" if scope == "g" else "ученика"
    rows = [
        [InlineKeyboardButton(
            text=display_period(p), callback_data=f"subovr:p:{group_id}:{scope}:{p}",
        )]
        for p in _override_periods()
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_billing:{group_id}")])
    await callback.message.edit_text(
        f"<b>📅 Цена абонемента на месяц ({label})</b>\n\nВыберите месяц:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("subovr:p:"))
async def cb_subovr_pick_target(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, group_id, scope, period = callback.data.split(":", 4)

    if scope == "g":
        await state.set_state(GroupBillingStates.entering_override_amount)
        await state.update_data(ovr_group_id=group_id, ovr_period=period, ovr_student_id=None)
        await callback.message.edit_text(
            f"<b>📅 {display_period(period)} — вся группа</b>\n\n"
            "Введите цену абонемента на этот месяц в рублях.\n"
            "<b>0</b> — в этом месяце не начислять никому.",
            reply_markup=kb_back(f"group_billing:{group_id}"),
        )
        await callback.answer()
        return

    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not students:
        await callback.answer("В группе нет учеников", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=s.name, callback_data=f"subovr:st:{group_id}:{period}:{s.student_id}",
        )]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_billing:{group_id}")])
    await callback.message.edit_text(
        f"<b>👤 {display_period(period)} — выберите ученика:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("subovr:st:"))
async def cb_subovr_pick_student(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, group_id, period, student_id = callback.data.split(":", 4)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await state.set_state(GroupBillingStates.entering_override_amount)
    await state.update_data(ovr_group_id=group_id, ovr_period=period, ovr_student_id=student_id)
    await callback.message.edit_text(
        f"<b>👤 {display_period(period)} — {student.name}</b>\n\n"
        "Введите цену абонемента на этот месяц в рублях.\n"
        "<b>0</b> — в этом месяце ученику не начислять.",
        reply_markup=kb_back(f"group_billing:{group_id}"),
    )
    await callback.answer()


@router.message(GroupBillingStates.entering_override_amount)
async def subovr_amount_entered(
    message: Message, state: FSMContext,
    group_repo: GroupRepository,
    subscription_override_repo: SubscriptionOverrideRepository,
    student_repo: StudentRepository,
) -> None:
    v = _parse_non_negative_int(message.text or "")
    if v is None:
        await message.answer("Нужно целое число рублей (0 — не начислять). Введите ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    group_id = data["ovr_group_id"]
    period = data["ovr_period"]
    student_id = data.get("ovr_student_id")
    group = await group_repo.get_by_id(group_id)
    if not group:
        await message.answer("Группа не найдена.", reply_markup=kb_back("admin:branches"))
        return
    await subscription_override_repo.upsert(group_id, period, student_id, v)
    logger.info("Переопределение абонемента %s %s student=%s → %d ₽",
                group_id, period, student_id or "вся группа", v)
    text, kb = await _billing_view(group, subscription_override_repo, student_repo)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("subovr:list:"))
async def cb_subovr_list(
    callback: CallbackQuery, user: User | None,
    subscription_override_repo: SubscriptionOverrideRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 2)[2]
    overrides = sorted(
        await subscription_override_repo.get_for_group(group_id),
        key=lambda o: (o.period_month, o.student_id or ""),
    )
    if not overrides:
        await callback.answer("Переопределений нет", show_alert=True)
        return
    rows = []
    for o in overrides:
        if o.student_id:
            student = await student_repo.get_by_id(o.student_id)
            who = student.name if student else o.student_id
        else:
            who = "вся группа"
        amount = f"{o.amount} ₽" if o.amount > 0 else "не начислять"
        sid_part = o.student_id or "-"
        rows.append([InlineKeyboardButton(
            text=f"✖ {display_period(o.period_month)} · {who} — {amount}",
            callback_data=f"subovr:del:{group_id}:{o.period_month}:{sid_part}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"group_billing:{group_id}")])
    await callback.message.edit_text(
        "<b>🗑 Сбросить переопределение</b>\n\nНажмите на строку, чтобы удалить её:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("subovr:del:"))
async def cb_subovr_delete(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    subscription_override_repo: SubscriptionOverrideRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, group_id, period, sid_part = callback.data.split(":", 4)
    student_id = None if sid_part == "-" else sid_part
    ok = await subscription_override_repo.delete(group_id, period, student_id)
    if not ok:
        await callback.answer("Уже удалено", show_alert=True)
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    text, kb = await _billing_view(group, subscription_override_repo, student_repo)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Сброшено")
