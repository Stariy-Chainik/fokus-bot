from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, PaymentRepository,
    BranchRepository, GroupRepository, StudentGroupRepository,
)
from bot.services import PaymentService
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period
from bot.handlers.access import is_admin as _is_admin

from ._base import (
    router, _confirming_in_progress,
)
from .helpers import (
    _periods_only_buttons,
)

logger = logging.getLogger(__name__)


# ─── Подтверждение оплаты: период → филиал → группа → ученик → счета ─────────

@router.callback_query(F.data == "bills:confirm_payment")
async def cb_confirm_payment_start(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "<b>Подтвердить оплату — выберите период:</b>",
        reply_markup=_periods_only_buttons("pcp", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pcp:"))
async def cb_confirm_payment_choose_branch(
    callback: CallbackQuery, user: User | None,
    branch_repo: BranchRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    students = await student_repo.get_all()
    sg_map = await student_group_repo.get_map_by_student()
    has_no_group = any(not sg_map.get(s.student_id) for s in students)

    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"pcpb:{period}:{b.branch_id}")]
        for b in branches
    ]
    if has_no_group:
        rows.append([InlineKeyboardButton(text="📋 Без группы", callback_data=f"pcpb:{period}:none")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="bills:confirm_payment")])

    if not rows[:-1]:
        await callback.message.edit_text(
            "Филиалов и учеников без группы нет.", reply_markup=kb_back("admin:menu"),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<b>{display_period(period)} — выберите филиал:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pcpb:"))
async def cb_confirm_payment_choose_group(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, branch_id = callback.data.split(":", 2)

    if branch_id == "none":
        sg_map = await student_group_repo.get_map_by_student()
        students = sorted(
            [s for s in await student_repo.get_all() if not sg_map.get(s.student_id)],
            key=lambda s: s.name,
        )
        if not students:
            await callback.message.edit_text(
                "Учеников без группы нет.", reply_markup=kb_back(f"pcp:{period}"),
            )
            await callback.answer()
            return
        rows = [
            [InlineKeyboardButton(text=s.name, callback_data=f"pcps:{period}:none:{s.student_id}")]
            for s in students
        ]
        rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"pcp:{period}")])
        await callback.message.edit_text(
            f"<b>{display_period(period)} — без группы — выберите ученика:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return

    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    if not groups:
        await callback.message.edit_text(
            "В филиале нет групп.", reply_markup=kb_back(f"pcp:{period}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"pcpg:{period}:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"pcp:{period}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pcpg:"))
async def cb_confirm_payment_choose_student(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id = callback.data.split(":", 2)
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not students:
        await callback.message.edit_text(
            "В группе нет учеников.",
            reply_markup=kb_back(f"pcpb:{period}:{group.branch_id}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"pcps:{period}:{group_id}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"pcpb:{period}:{group.branch_id}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — {group.name} — выберите ученика:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pcps:"))
async def cb_pay_pick_invoice(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period_month, group_id, student_id = callback.data.split(":", 3)
    back_cb = (
        f"pcpb:{period_month}:none" if group_id == "none"
        else f"pcpg:{period_month}:{group_id}"
    )
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    invoices = await payment_service.get_or_create_invoices_for_student_period(
        student, period_month,
    )
    if not invoices:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет занятий.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    for p in invoices:
        paid = p.status.value == "paid"
        icon = "✅" if paid else "⏳"
        label = f"{icon} {p.teacher_name or '—'} — {p.total_amount} руб."
        if paid:
            rows.append([InlineKeyboardButton(text=label, callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"pay_invoice:{p.payment_id}:{group_id}",
            )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    await callback.message.edit_text(
        f"<b>{student.name}</b> — {display_period(period_month)}\n"
        "Выберите счёт для подтверждения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_invoice:"))
async def cb_pay_confirm(
    callback: CallbackQuery, user: User | None,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    # pay_invoice:{payment_id}[:{group_id}]
    payment_id = parts[1]
    group_id = parts[2] if len(parts) > 2 else "none"
    payment = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id), None,
    )
    if not payment:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    pick_cb = f"pcps:{payment.period_month}:{group_id}:{payment.student_id}"
    if payment.status.value == "paid":
        await callback.message.edit_text(
            f"Счёт {payment.payment_id} уже оплачен.", reply_markup=kb_back(pick_cb),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"<b>Подтвердить оплату счёта {payment.payment_id}?</b>\n"
        f"Ученик: {payment.student_name}\n"
        f"Педагог: {payment.teacher_name or '—'}\n"
        f"Период: {display_period(payment.period_month)}\n"
        f"Сумма: {payment.total_amount} руб.",
        reply_markup=kb_confirm(
            f"do_confirm_payment:{payment.payment_id}:{group_id}", pick_cb,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_confirm_payment:"))
async def cb_do_confirm_payment(
    callback: CallbackQuery, user: User | None, payment_service: PaymentService,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    payment_id = parts[1]
    group_id = parts[2] if len(parts) > 2 else "none"
    payment = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id), None,
    )
    back_cb = (
        f"pcps:{payment.period_month}:{group_id}:{payment.student_id}"
        if payment else "admin:menu"
    )

    if payment_id in _confirming_in_progress:
        logger.warning("Двойное подтверждение payment_id=%s tg_id=%s", payment_id, callback.from_user.id)
        await callback.answer("Оплата уже обрабатывается", show_alert=True)
        return
    _confirming_in_progress.add(payment_id)

    try:
        ok = await payment_service.confirm_payment(payment_id, callback.from_user.id)
        if ok:
            await callback.message.edit_text(f"Оплата {payment_id} подтверждена.", reply_markup=kb_back(back_cb))
        else:
            await callback.message.edit_text("Счёт уже оплачен или не найден.", reply_markup=kb_back(back_cb))
    except Exception as exc:
        logger.error("Ошибка подтверждения оплаты %s: %s", payment_id, exc)
        await callback.message.edit_text("Ошибка при подтверждении оплаты.", reply_markup=kb_back(back_cb))
    finally:
        _confirming_in_progress.discard(payment_id)

    await callback.answer()
