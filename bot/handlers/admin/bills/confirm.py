from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, PaymentRepository,
    BranchRepository, GroupRepository, StudentGroupRepository,
)
from bot.services import PaymentService
from bot.keyboards.admin import kb_back, kb_confirm
from bot.utils.dates import display_period, format_date_short_with_wd
from bot.handlers.filters import AdminOnly
from bot.services.payment_methods import ADMIN_MANUAL
from bot.services.payment_service import SUBSCRIPTION_KEY_PREFIX

from bot.utils.callbacks import (
    DoConfirmPaymentCb,
    PayConfirmBranchCb,
    PayConfirmGroupCb,
    PayConfirmPeriodCb,
    PayConfirmStudentCb,
    PayInvoiceCb,
    PaySelectApplyCb,
    PaySelectLessonToggleCb,
    PaySelectLessonsCb,
)
from bot.services.rosters import group_members
from ._base import (
    router, _confirming_in_progress,
)
from .helpers import (
    _periods_only_buttons,
)

logger = logging.getLogger(__name__)


# ─── Подтверждение оплаты: период → филиал → группа → ученик → счета ─────────

@router.callback_query(F.data == "bills:confirm_payment", AdminOnly())
async def cb_confirm_payment_start(callback: CallbackQuery, user: User) -> None:
    await callback.message.edit_text(
        "<b>Подтвердить оплату — выберите период:</b>",
        reply_markup=_periods_only_buttons("pcp", back_cb="admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pcp:"), AdminOnly())
async def cb_confirm_payment_choose_branch(
    callback: CallbackQuery, user: User,
    branch_repo: BranchRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    period = PayConfirmPeriodCb.unpack(callback.data).period
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


@router.callback_query(F.data.startswith("pcpb:"), AdminOnly())
async def cb_confirm_payment_choose_group(
    callback: CallbackQuery, user: User,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    cb = PayConfirmBranchCb.unpack(callback.data)
    period, branch_id = cb.period, cb.branch_id

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


@router.callback_query(F.data.startswith("pcpg:"), AdminOnly())
async def cb_confirm_payment_choose_student(
    callback: CallbackQuery, user: User,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository,
) -> None:
    cb = PayConfirmGroupCb.unpack(callback.data)
    period, group_id = cb.period, cb.group_id
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    students = await group_members(student_repo, student_group_repo, group_id)
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


@router.callback_query(F.data.startswith("pcps:"), AdminOnly())
async def cb_pay_pick_invoice(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    cb = PayConfirmStudentCb.unpack(callback.data)
    period_month, group_id, student_id = cb.period, cb.group_id, cb.student_id
    back_cb = (
        f"pcpb:{period_month}:none" if group_id == "none"
        else f"pcpg:{period_month}:{group_id}"
    )
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    ledgers = await payment_service.ledger_for(student, period_month)
    invoices = [r for ledger in ledgers.values() for r in ([*ledger.paid_rows] + ([ledger.pending] if ledger.pending else []))]
    labels = {tid: ledger.name for tid, ledger in ledgers.items()}  # групповые позиции — названием группы
    if not invoices:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет занятий.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    for p in sorted(invoices, key=lambda x: (labels.get(x.teacher_id) or x.teacher_name or "", x.status.value != "paid", x.paid_at or "")):
        paid = p.status.value == "paid"
        if not paid and p.total_amount <= 0:
            continue  # остаток 0 — платить нечего
        label = labels.get(p.teacher_id) or p.teacher_name or "—"
        if paid:
            when = f" ({(p.paid_at or '')[:10]})" if p.paid_at else ""
            rows.append([InlineKeyboardButton(
                text=f"✅ {label} — {p.total_amount} руб.{when}", callback_data="noop",
            )])
        else:
            # По занятиям можно отметить выборочно; абонемент — только целиком
            target = (f"pay_invoice:{p.payment_id}:{group_id}"
                      if p.teacher_id.startswith(SUBSCRIPTION_KEY_PREFIX)
                      else f"paysel:{p.payment_id}:{group_id}")
            rows.append([InlineKeyboardButton(
                text=f"⏳ {label} — к доплате {p.total_amount} руб.",
                callback_data=target,
            )])
    if not rows:
        rows.append([InlineKeyboardButton(text="✅ Всё оплачено", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    await callback.message.edit_text(
        f"<b>{student.name}</b> — {display_period(period_month)}\n"
        "Выберите счёт для подтверждения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_invoice:"), AdminOnly())
async def cb_pay_confirm(
    callback: CallbackQuery, user: User,
    payment_repo: PaymentRepository,
) -> None:
    cb = PayInvoiceCb.unpack(callback.data)  # pay_invoice:{payment_id}[:{group_id}]
    payment_id, group_id = cb.payment_id, cb.group_id
    payment = next(
        (p for p in await payment_repo.get_all() if p.payment_id == payment_id), None,
    )
    if not payment:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    pick_cb = f"pcps:{payment.period_month}:{group_id}:{payment.student_id}"
    if payment.status.value == "paid" or payment.total_amount <= 0:
        await callback.message.edit_text(
            f"Счёт {payment.payment_id} уже оплачен." if payment.status.value == "paid"
            else f"По счёту {payment.payment_id} остаток 0 — платить нечего.",
            reply_markup=kb_back(pick_cb),
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


@router.callback_query(F.data.startswith("do_confirm_payment:"), AdminOnly())
async def cb_do_confirm_payment(
    callback: CallbackQuery, user: User, payment_service: PaymentService,
    payment_repo: PaymentRepository,
) -> None:

    cb = DoConfirmPaymentCb.unpack(callback.data)
    payment_id, group_id = cb.payment_id, cb.group_id
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
        ok = await payment_service.confirm_payment(
            payment_id, callback.from_user.id, ADMIN_MANUAL,
        )
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


# ─── Выборочная отметка занятий педагога ────────────────────────────────────
# Админ отмечает галочками, какие занятия оплачены. Сумма считается по отметкам
# и зачитывается через record_payment: остаток педагога закрывается от самых
# ранних занятий (порядок дат), поэтому суммарно учёт всегда сходится.

def _sel_screen(student_name: str, ledger, marks: list, chosen: set) -> tuple:
    lines = [f"<b>{student_name}</b> — {ledger.name}",
             f"Начислено {ledger.accrued} руб., оплачено {ledger.paid}, к доплате {ledger.remainder}",
             "", "Отметьте занятия, которые оплачены:"]
    rows: list[list[InlineKeyboardButton]] = []
    total = 0
    for i, m in enumerate(marks):
        when = format_date_short_with_wd(m["date"])
        label = f"{when} · {m['duration_min']} мин · {m['amount']} руб."
        if m["paid"]:
            rows.append([InlineKeyboardButton(text=f"✅ {label}", callback_data="noop")])
            continue
        mark = "☑️" if m["lesson_id"] in chosen else "⬜"
        if m["lesson_id"] in chosen:
            total += m["amount"]
        rows.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"pslt:{i}")])
    if total > 0:
        rows.append([InlineKeyboardButton(
            text=f"✅ Подтвердить оплату {total} руб.", callback_data="pslgo")])
    return "\n".join(lines), rows, total


async def _render_selection(callback: CallbackQuery, state: FSMContext,
                            student_repo: StudentRepository, payment_service: PaymentService) -> None:
    data = await state.get_data()
    student = await student_repo.get_by_id(data["psel_student"])
    marks, ledger = await payment_service.teacher_lesson_marks(
        student, data["psel_period"], data["psel_teacher"],
    )
    back_cb = f"pcps:{data['psel_period']}:{data['psel_group']}:{data['psel_student']}"
    if not marks or ledger is None:
        await callback.message.edit_text("Занятий для отметки нет.", reply_markup=kb_back(back_cb))
        return
    chosen = set(data.get("psel_chosen") or [])
    text, rows, _ = _sel_screen(student.name, ledger, marks, chosen)
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("paysel:"), AdminOnly())
async def cb_pay_select_lessons(
    callback: CallbackQuery, user: User, state: FSMContext,
    student_repo: StudentRepository, payment_repo: PaymentRepository,
    payment_service: PaymentService,
) -> None:
    cb = PaySelectLessonsCb.unpack(callback.data)
    payment_id, group_id = cb.payment_id, cb.group_id
    payment = await payment_repo.get_by_id(payment_id)
    if payment is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await state.update_data(
        psel_student=payment.student_id, psel_period=payment.period_month,
        psel_teacher=payment.teacher_id, psel_group=group_id, psel_chosen=[],
    )
    await _render_selection(callback, state, student_repo, payment_service)
    await callback.answer()


@router.callback_query(F.data.startswith("pslt:"), AdminOnly())
async def cb_pay_select_toggle(
    callback: CallbackQuery, user: User, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    data = await state.get_data()
    if not data.get("psel_student"):
        await callback.answer("Экран устарел, откройте счёт заново", show_alert=True)
        return
    idx = PaySelectLessonToggleCb.unpack(callback.data).idx
    student = await student_repo.get_by_id(data["psel_student"])
    marks, _ = await payment_service.teacher_lesson_marks(
        student, data["psel_period"], data["psel_teacher"],
    )
    if idx >= len(marks):
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    chosen = set(data.get("psel_chosen") or [])
    chosen.symmetric_difference_update({marks[idx]["lesson_id"]})
    await state.update_data(psel_chosen=list(chosen))
    await _render_selection(callback, state, student_repo, payment_service)
    await callback.answer()


@router.callback_query(F.data == "pslgo", AdminOnly())
async def cb_pay_select_confirm(
    callback: CallbackQuery, user: User, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    data = await state.get_data()
    if not data.get("psel_student"):
        await callback.answer("Экран устарел, откройте счёт заново", show_alert=True)
        return
    student = await student_repo.get_by_id(data["psel_student"])
    marks, ledger = await payment_service.teacher_lesson_marks(
        student, data["psel_period"], data["psel_teacher"],
    )
    chosen = set(data.get("psel_chosen") or [])
    picked = [m for m in marks if m["lesson_id"] in chosen and not m["paid"]]
    total = sum(m["amount"] for m in picked)
    if total <= 0:
        await callback.answer("Не отмечено ни одного занятия", show_alert=True)
        return
    back_cb = f"paysel:{ledger.pending.payment_id}:{data['psel_group']}" if ledger.pending else "admin:menu"
    dates = ", ".join(format_date_short_with_wd(m["date"]) for m in picked)
    await callback.message.edit_text(
        f"<b>Подтвердить оплату?</b>\n"
        f"Ученик: {student.name}\n"
        f"{'Группа' if ledger.group else 'Педагог'}: {ledger.name}\n"
        f"Период: {display_period(data['psel_period'])}\n"
        f"Занятий: {len(picked)} ({dates})\n"
        f"Сумма: <b>{total} руб.</b>\n\n"
        f"<i>Сумма закроет остаток начиная с самых ранних неоплаченных занятий.</i>",
        reply_markup=kb_confirm(f"pslok:{total}", back_cb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pslok:"), AdminOnly())
async def cb_pay_select_apply(
    callback: CallbackQuery, user: User, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    data = await state.get_data()
    if not data.get("psel_student"):
        await callback.answer("Экран устарел, откройте счёт заново", show_alert=True)
        return
    amount = PaySelectApplyCb.unpack(callback.data).amount
    student_id, period = data["psel_student"], data["psel_period"]
    teacher_id, group_id = data["psel_teacher"], data["psel_group"]
    guard_key = f"{student_id}:{period}:{teacher_id}"
    if guard_key in _confirming_in_progress:
        await callback.answer("Оплата уже обрабатывается", show_alert=True)
        return
    _confirming_in_progress.add(guard_key)
    back_cb = f"pcps:{period}:{group_id}:{student_id}"
    try:
        student = await student_repo.get_by_id(student_id)
        credited, rows_count = await payment_service.record_payment(
            student_id, student.name if student else student_id, period, amount,
            callback.from_user.id, [teacher_id], "отмечено вручную", ADMIN_MANUAL,
            lesson_ids=list(data.get("psel_chosen") or []),
        )
        await state.update_data(psel_chosen=[])
        if credited > 0:
            logger.info("Админ %s отметил оплату %d руб.: %s %s %s",
                        callback.from_user.id, credited, student_id, period, teacher_id)
            await callback.message.edit_text(
                f"✅ Оплата {credited} руб. отмечена ({rows_count} записей).",
                reply_markup=kb_back(back_cb),
            )
        else:
            await callback.message.edit_text("Нечего подтверждать — остаток уже закрыт.",
                                             reply_markup=kb_back(back_cb))
    except Exception as exc:
        logger.error("Ошибка отметки оплаты %s: %s", guard_key, exc)
        await callback.message.edit_text("Ошибка при отметке оплаты.", reply_markup=kb_back(back_cb))
    finally:
        _confirming_in_progress.discard(guard_key)
    await callback.answer()
