"""Биллинг группы: общие helpers (клавиатура, текст, рендер экрана).

Хендлеры живут в billing_modes.py (экран/off/per-visit) и
billing_subscription.py (абонемент + переопределения цены).
"""
from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import GroupBillingMode
from bot.repositories import StudentRepository, SubscriptionOverrideRepository
from bot.utils.dates import display_period

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


async def _billing_view(
    group,
    override_repo: SubscriptionOverrideRepository, student_repo: StudentRepository,
    note: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    """(text, kb) экрана биллинга группы: базовый текст + переопределения + note."""
    text = _billing_text(
        group.name, group.billing_mode,
        group.price_short, group.duration_short,
        group.price_full, group.duration_full,
    ) + await _overrides_block(group, override_repo, student_repo) + note
    return text, _kb_group_billing(group.group_id, group.billing_mode)


def _override_periods() -> list[str]:
    """Прошлый, текущий и следующий месяцы."""
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    return [(today + relativedelta(months=i)).strftime("%Y-%m") for i in (-1, 0, 1)]


def _parse_positive_int(raw: str) -> int | None:
    raw = (raw or "").strip().replace(" ", "")
    if not raw.isdigit():
        return None
    v = int(raw)
    return v if v > 0 else None


def _parse_non_negative_int(raw: str) -> int | None:
    raw = (raw or "").strip().replace(" ", "")
    return int(raw) if raw.isdigit() else None
