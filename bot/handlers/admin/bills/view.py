from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, PaymentRepository,
    BranchRepository, GroupRepository, StudentGroupRepository,
    ClientRepository,
)
from bot.services import PaymentService
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period, format_date_short_with_wd
from bot.handlers.access import is_admin as _is_admin

from ._base import (
    router, _group_sending,
)
from .helpers import (
    _send_bill_to_parents, _student_group_names, _periods_only_buttons,
)

logger = logging.getLogger(__name__)


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
                "Учеников без группы нет.", reply_markup=kb_back(f"bvp:{period}"),
            )
            await callback.answer()
            return
        rows = [
            [InlineKeyboardButton(text=s.name, callback_data=f"bvs:{period}:none:{s.student_id}")]
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
            reply_markup=kb_back(f"bvb:{period}:{group.branch_id}"),
        )
        await callback.answer()
        return
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="📨 Отправить счета всей группе",
            callback_data=f"bill_group_send:{period}:{group_id}",
        )],
    ]
    rows += [
        [InlineKeyboardButton(text=s.name, callback_data=f"bvs:{period}:{group_id}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"bvb:{period}:{group.branch_id}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — {group.name}</b>\n"
        "Отправить счета всей группе или выберите ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bill_group_send:"))
async def cb_bill_group_send(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    payment_service: PaymentService, client_repo: ClientRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, gid = callback.data.split(":", 2)
    group = await group_repo.get_by_id(gid)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    lock_key = f"{gid}:{period}"
    if lock_key in _group_sending:
        await callback.answer("Рассылка уже выполняется", show_alert=True)
        return
    _group_sending.add(lock_key)
    try:
        member_ids = set(await student_group_repo.get_students_for_group(gid))
        students = [s for s in await student_repo.get_all() if s.student_id in member_ids]

        sent_students = 0
        no_parent_students = 0
        failed_students = 0
        total_sent_to = 0
        for s in students:
            bills = await payment_service.compute_bills_for_student_period(s.student_id, period)
            if not bills:
                continue
            group_names = await _student_group_names(s.student_id, student_group_repo, group_repo)
            total_rec, sent_to, _ = await _send_bill_to_parents(
                callback, s, period, bills, group_names, payment_service, client_repo,
            )
            if total_rec == 0:
                no_parent_students += 1
            elif sent_to == 0:
                failed_students += 1
            else:
                sent_students += 1
                total_sent_to += sent_to

        back_cb = f"bvg:{period}:{gid}"
        lines = [
            f"<b>📨 Рассылка по группе «{group.name}»</b>",
            f"Период: {display_period(period)}",
            "",
            f"✅ Отправлено родителям: {sent_students} учеников ({total_sent_to} сообщений)",
        ]
        if no_parent_students:
            lines.append(f"⚠️ Без привязанного родителя: {no_parent_students}")
        if failed_students:
            lines.append(f"❌ Не доставлено (бот заблокирован): {failed_students}")
        logger.info("Admin group bill sent group=%s period=%s sent=%s", gid, period, sent_students)
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К ученикам", callback_data=back_cb)],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")],
            ]),
        )
        await callback.answer()
    finally:
        _group_sending.discard(lock_key)


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
    _, period_month, group_id, student_id = callback.data.split(":", 3)
    back_cb = (
        f"bvb:{period_month}:none" if group_id == "none"
        else f"bvg:{period_month}:{group_id}"
    )
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
    if not bills:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period_month)} нет занятий к оплате.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    payments = await payment_repo.get_by_student_and_period(student_id, period_month)
    pay_by_teacher = {p.teacher_id: p for p in payments}

    lines = [f"<b>Счёт: {student.name}</b>", f"Период: {display_period(period_month)}", ""]
    grand_total = 0
    for teacher_id, agg in bills.items():
        subtotal = agg["total"]
        grand_total += subtotal
        p = pay_by_teacher.get(teacher_id)
        if p and p.status.value == "paid":
            status = f"✅ Оплачен ({p.paid_at or ''})"
        elif p:
            status = "📋 Ожидает оплаты"
        else:
            status = "⏳ Счёт не создан"
        lines.append(f"👨‍🏫 <b>{agg['name']}</b> — {subtotal} руб. — {status}")
        items = agg["items"]
        individual = [b for b in items if b.lesson_type != "group"]
        group_items = [b for b in items if b.lesson_type == "group"]
        if individual:
            lines.append("  <i>Индивидуальные:</i>")
            cur_date: str | None = None
            for b in sorted(individual, key=lambda x: x.date):
                if b.date != cur_date:
                    cur_date = b.date
                    lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
                lines.append(f"    · {b.duration_min} мин · {b.amount} руб.")
        if group_items:
            group_total = sum(b.amount for b in group_items)
            lines.append(f"  <i>Групповые ({len(group_items)} посещений, {group_total} руб.):</i>")
            cur_date = None
            for b in sorted(group_items, key=lambda x: x.date):
                if b.date != cur_date:
                    cur_date = b.date
                    lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
                lines.append(f"    · {b.duration_min} мин · {b.amount} руб.")
        lines.append("")
    lines.append(f"Итого: {grand_total} руб.")

    rows = [[InlineKeyboardButton(
        text="📤 Отправить родителю",
        callback_data=f"bill_send:{student_id}:{period_month}:{group_id}",
    )]]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()

