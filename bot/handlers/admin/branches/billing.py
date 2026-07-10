from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode
from datetime import date

from bot.repositories import (
    GroupRepository, StudentRepository, StudentGroupRepository,
    SubscriptionOverrideRepository,
)
from bot.services import PaymentService
from bot.states import (
    GroupBillingStates,
)
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Биллинг ученикам (per-visit) ────────────────────────────────────────────

_MODE_TITLES = {
    GroupBillingMode.NONE: "не выставляется (группа бесплатная)",
    GroupBillingMode.PER_VISIT: "по посещениям (per-visit)",
    GroupBillingMode.SUBSCRIPTION: "абонемент (фиксированная сумма в месяц)",
}


def _kb_group_billing(group_id: str, mode: GroupBillingMode) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if mode == GroupBillingMode.PER_VISIT:
        rows.append([InlineKeyboardButton(
            text="✏️ Изменить тарифы", callback_data=f"group_billing_edit:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="💳 Переключить на абонемент", callback_data=f"group_billing_sub:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="🚫 Отключить биллинг", callback_data=f"group_billing_off:{group_id}",
        )])
    elif mode == GroupBillingMode.SUBSCRIPTION:
        rows.append([InlineKeyboardButton(
            text="✏️ Изменить цену абонемента", callback_data=f"group_billing_sub:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="📅 Цена на месяц (вся группа)", callback_data=f"subovr:m:{group_id}:g",
        )])
        rows.append([InlineKeyboardButton(
            text="👤 Цена на месяц (ученик)", callback_data=f"subovr:m:{group_id}:s",
        )])
        rows.append([InlineKeyboardButton(
            text="🗑 Сбросить переопределение", callback_data=f"subovr:list:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="💰 Переключить на посещения", callback_data=f"group_billing_edit:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="🚫 Отключить биллинг", callback_data=f"group_billing_off:{group_id}",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="💰 Включить биллинг по посещениям",
            callback_data=f"group_billing_edit:{group_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="💳 Включить абонемент (₽/мес)",
            callback_data=f"group_billing_sub:{group_id}",
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
    elif mode == GroupBillingMode.SUBSCRIPTION:
        lines += [
            "",
            f"💳 Абонемент: <b>{price_full} ₽ в месяц</b> с ученика",
            "",
            "Сумма фиксированная — количество занятий не влияет.",
            "Начисляется всем ученикам группы за месяц, в котором",
            "у группы было хотя бы одно занятие (каникулы — не платят).",
            "Присутствующих на занятии отмечать не нужно.",
        ]
    return "\n".join(lines)


async def _overrides_block(
    group, override_repo: SubscriptionOverrideRepository, student_repo: StudentRepository,
) -> str:
    """Блок активных переопределений цены (для SUBSCRIPTION-группы), либо ''."""
    if group.billing_mode != GroupBillingMode.SUBSCRIPTION:
        return ""
    overrides = sorted(
        await override_repo.get_for_group(group.group_id),
        key=lambda o: (o.period_month, o.student_id or ""),
    )
    if not overrides:
        return ""
    lines = ["", "📌 <b>Переопределения цены:</b>"]
    for o in overrides:
        if o.student_id:
            student = await student_repo.get_by_id(o.student_id)
            who = student.name if student else o.student_id
        else:
            who = "вся группа"
        amount = f"{o.amount} ₽" if o.amount > 0 else "не начислять"
        lines.append(f"  • {display_period(o.period_month)} · {who} — {amount}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("group_billing:"))
async def cb_group_billing(
    callback: CallbackQuery, user: User | None, group_repo: GroupRepository,
    subscription_override_repo: SubscriptionOverrideRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    text = _billing_text(
        group.name, group.billing_mode,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    ) + await _overrides_block(group, subscription_override_repo, student_repo)
    await callback.message.edit_text(
        text,
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
    text = _billing_text(
        group.name, GroupBillingMode.SUBSCRIPTION,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    ) + await _overrides_block(group, subscription_override_repo, student_repo) + note
    await callback.message.edit_text(
        text, reply_markup=_kb_group_billing(group_id, GroupBillingMode.SUBSCRIPTION),
    )
    await callback.answer()


# ─── Переопределение цены абонемента на месяц (ученик / вся группа) ──────────

def _override_periods() -> list[str]:
    """Прошлый, текущий и следующий месяцы."""
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    return [(today + relativedelta(months=i)).strftime("%Y-%m") for i in (-1, 0, 1)]


def _parse_non_negative_int(raw: str) -> int | None:
    raw = (raw or "").strip().replace(" ", "")
    return int(raw) if raw.isdigit() else None


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
    text = _billing_text(
        group.name, group.billing_mode,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    ) + await _overrides_block(group, subscription_override_repo, student_repo)
    await message.answer(text, reply_markup=_kb_group_billing(group_id, group.billing_mode))


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
    text = _billing_text(
        group.name, group.billing_mode,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    ) + await _overrides_block(group, subscription_override_repo, student_repo)
    await callback.message.edit_text(
        text, reply_markup=_kb_group_billing(group_id, group.billing_mode),
    )
    await callback.answer("Сброшено")


