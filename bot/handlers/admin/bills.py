from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, PaymentRepository, TeacherRepository,
    BranchRepository, GroupRepository,
)
from bot.services import PaymentService
from bot.keyboards.admin import kb_student_list, kb_back, kb_confirm
from bot.utils.dates import display_period, format_date_display

logger = logging.getLogger(__name__)
router = Router(name="admin_bills")

_confirming_in_progress: set[str] = set()
_sending_in_progress: set[str] = set()


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _period_buttons(student_id: str, action_prefix: str) -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{student_id}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _periods_only_buttons(action_prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"{action_prefix}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Просмотр счёта: период → филиал → группа → ученик → счёт ────────────────

@router.callback_query(F.data == "bills:view")
async def cb_bills_choose_period(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "<b>Выберите период:</b>",
        reply_markup=_periods_only_buttons("bvp", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bvp:"))
async def cb_bills_choose_branch(
    callback: CallbackQuery, user: User | None,
    branch_repo: BranchRepository, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    students = await student_repo.get_all()
    has_no_group = any(not s.group_id for s in students)

    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"bvb:{period}:{b.branch_id}")]
        for b in branches
    ]
    if has_no_group:
        rows.append([InlineKeyboardButton(text="📋 Без группы", callback_data=f"bvb:{period}:none")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="bills:view")])

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


@router.callback_query(F.data.startswith("bvb:"))
async def cb_bills_choose_group(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, branch_id = callback.data.split(":", 2)

    if branch_id == "none":
        students = sorted(
            [s for s in await student_repo.get_all() if not s.group_id],
            key=lambda s: s.name,
        )
        if not students:
            await callback.message.edit_text(
                "Учеников без группы нет.", reply_markup=kb_back(f"bvp:{period}"),
            )
            await callback.answer()
            return
        rows = [
            [InlineKeyboardButton(text=s.name, callback_data=f"bvs:{period}:{s.student_id}")]
            for s in students
        ]
        rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"bvp:{period}")])
        await callback.message.edit_text(
            f"<b>{display_period(period)} — без группы — выберите ученика:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return

    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: (g.sort_order, g.name))
    if not groups:
        await callback.message.edit_text(
            "В филиале нет групп.", reply_markup=kb_back(f"bvp:{period}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"bvg:{period}:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"bvp:{period}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bvg:"))
async def cb_bills_choose_student(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id = callback.data.split(":", 2)
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    students = sorted(
        [s for s in await student_repo.get_all() if s.group_id == group_id],
        key=lambda s: s.name,
    )
    if not students:
        await callback.message.edit_text(
            "В группе нет учеников.",
            reply_markup=kb_back(f"bvb:{period}:{group.branch_id}"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"bvs:{period}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"bvb:{period}:{group.branch_id}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — {group.name} — выберите ученика:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bvs:"))
async def cb_bills_show(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    payment_repo: PaymentRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period_month, student_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
    if not bills:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет индивидуальных занятий.",
            reply_markup=kb_back("admin:menu"),
        )
        await callback.answer()
        return

    payments = await payment_repo.get_by_student_and_period(student_id, period_month)
    pay_by_teacher = {p.teacher_id: p for p in payments}

    not_submitted_ids = await payment_service.teachers_not_submitted(
        list(bills.keys()), period_month,
    )

    lines = [f"<b>Счёт: {student.name}</b>", f"Период: {display_period(period_month)}", ""]
    grand_total = 0
    for teacher_id, agg in bills.items():
        subtotal = agg["total"]
        grand_total += subtotal
        p = pay_by_teacher.get(teacher_id)
        if p and p.status.value == "paid":
            status = f"✅ Оплачен ({p.paid_at or ''})"
        elif teacher_id in not_submitted_ids:
            status = "🔴 Период не сдан педагогом"
        elif p:
            status = "📋 Ожидает оплаты"
        else:
            status = "⏳ Счёт не создан"
        lines.append(f"👨‍🏫 <b>{agg['name']}</b> — {subtotal} руб. — {status}")
        for b in agg["items"]:
            lines.append(f"  {format_date_display(b.date)} | {b.duration_min} мин | {b.amount} руб.")
        lines.append("")
    lines.append(f"Итого: {grand_total} руб.")

    rows = [[InlineKeyboardButton(
        text="📤 Отправить родителю", callback_data=f"bill_send:{student_id}:{period_month}",
    )]]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Отправка родителю (заглушка) ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("bill_send:"))
async def cb_bill_send(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository,
    teacher_repo: TeacherRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)

    lock_key = f"{student_id}:{period_month}"
    if lock_key in _sending_in_progress:
        await callback.answer("Отправка уже выполняется", show_alert=True)
        return
    _sending_in_progress.add(lock_key)
    try:
        student = await student_repo.get_by_id(student_id)
        if not student:
            await callback.answer("Ученик не найден", show_alert=True)
            return

        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        if not bills:
            await callback.answer("В счёте нет занятий", show_alert=True)
            return

        not_submitted = await payment_service.teachers_not_submitted(
            list(bills.keys()), period_month,
        )
        if not_submitted:
            names = []
            for tid in not_submitted:
                t = await teacher_repo.get_by_id(tid)
                names.append(t.name if t else tid)
            await callback.answer(
                "Период не сдан педагогами:\n" + "\n".join(names),
                show_alert=True,
            )
            return

        # Все сдали — создаём счета и шлём (заглушка).
        invoices = await payment_service.get_or_create_invoices_for_student_period(
            student, period_month,
        )
        logger.info(
            "Заглушка отправки счетов родителю student=%s period=%s invoices=%d",
            student_id, period_month, len(invoices),
        )
        await callback.answer(
            f"Счёт {student.name} за {display_period(period_month)} отправлен родителю (заглушка). "
            f"Счетов: {len(invoices)}.",
            show_alert=True,
        )
    finally:
        _sending_in_progress.discard(lock_key)


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
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    students = await student_repo.get_all()
    has_no_group = any(not s.group_id for s in students)

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
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, branch_id = callback.data.split(":", 2)

    if branch_id == "none":
        students = sorted(
            [s for s in await student_repo.get_all() if not s.group_id],
            key=lambda s: s.name,
        )
        if not students:
            await callback.message.edit_text(
                "Учеников без группы нет.", reply_markup=kb_back(f"pcp:{period}"),
            )
            await callback.answer()
            return
        rows = [
            [InlineKeyboardButton(text=s.name, callback_data=f"pcps:{period}:{s.student_id}")]
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
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id = callback.data.split(":", 2)
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    students = sorted(
        [s for s in await student_repo.get_all() if s.group_id == group_id],
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
        [InlineKeyboardButton(text=s.name, callback_data=f"pcps:{period}:{s.student_id}")]
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
    _, period_month, student_id = callback.data.split(":", 2)
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
            reply_markup=kb_back("admin:menu"),
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
            rows.append([InlineKeyboardButton(text=label, callback_data=f"pay_invoice:{p.payment_id}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])

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
    payment_id = callback.data.split(":", 1)[1]
    payment = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id), None,
    )
    if not payment:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    if payment.status.value == "paid":
        await callback.message.edit_text(
            f"Счёт {payment.payment_id} уже оплачен.", reply_markup=kb_back("admin:menu"),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"<b>Подтвердить оплату счёта {payment.payment_id}?</b>\n"
        f"Ученик: {payment.student_name}\n"
        f"Педагог: {payment.teacher_name or '—'}\n"
        f"Период: {display_period(payment.period_month)}\n"
        f"Сумма: {payment.total_amount} руб.",
        reply_markup=kb_confirm(f"do_confirm_payment:{payment.payment_id}", "admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_confirm_payment:"))
async def cb_do_confirm_payment(
    callback: CallbackQuery, user: User | None, payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    payment_id = callback.data.split(":", 1)[1]

    if payment_id in _confirming_in_progress:
        logger.warning("Двойное подтверждение payment_id=%s tg_id=%s", payment_id, callback.from_user.id)
        await callback.answer("Оплата уже обрабатывается", show_alert=True)
        return
    _confirming_in_progress.add(payment_id)

    try:
        ok = await payment_service.confirm_payment(payment_id, callback.from_user.id)
        if ok:
            await callback.message.edit_text(f"Оплата {payment_id} подтверждена.", reply_markup=kb_back("admin:menu"))
        else:
            await callback.message.edit_text("Счёт уже оплачен или не найден.", reply_markup=kb_back("admin:menu"))
    except Exception as exc:
        logger.error("Ошибка подтверждения оплаты %s: %s", payment_id, exc)
        await callback.message.edit_text("Ошибка при подтверждении оплаты.", reply_markup=kb_back("admin:menu"))
    finally:
        _confirming_in_progress.discard(payment_id)

    await callback.answer()
