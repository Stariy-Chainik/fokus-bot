"""Счета от педагога — расширенное право из BILLING_TEACHER_IDS.

Педагог из списка видит в меню «🧾 Счета моих групп» и может просматривать
и отправлять родителям счета учеников, но только по своим группам
(teacher_groups). Сам счёт — общий «Счёт ученика за период»: в него входят
занятия ученика у всех педагогов, как и в админском флоу.

Логика отправки и рендер переиспользуются из admin/bills (то же место,
те же локи — одновременная рассылка админом и педагогом не задвоится).
"""
from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, PaymentRepository, GroupRepository,
    TeacherGroupRepository, StudentGroupRepository, ClientRepository,
)
from bot.services import PaymentService
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period
from bot.handlers.access import TeacherUser, can_teacher_bill
from bot.handlers.admin.bills._base import _sending_in_progress, _group_sending
from bot.handlers.admin.bills.helpers import (
    _send_bill_to_parents, _student_group_names, _periods_only_buttons,
    _bill_detail_lines,
)

logger = logging.getLogger(__name__)
router = Router(name="teacher_bills")


async def _own_group_ids(user: TeacherUser, teacher_group_repo: TeacherGroupRepository) -> set[str]:
    return set(await teacher_group_repo.get_groups_for_teacher(user.teacher_id))


# ─── Период → группа → ученик ────────────────────────────────────────────────

@router.callback_query(F.data == "teacher:bills")
async def cb_tbills_choose_period(callback: CallbackQuery, user: User | None) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "<b>Счета моих групп — выберите период:</b>",
        reply_markup=_periods_only_buttons("tblp", back_cb="teacher:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tblp:"))
async def cb_tbills_choose_group(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    own_ids = await _own_group_ids(user, teacher_group_repo)
    groups = sorted(
        [g for gid in own_ids if (g := await group_repo.get_by_id(gid))],
        key=lambda g: (g.sort_order, g.name),
    )
    if not groups:
        await callback.message.edit_text(
            "У вас нет групп.", reply_markup=kb_back("teacher:bills"),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"tblg:{period}:{g.group_id}")]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:bills")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — выберите группу:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tblg:"))
async def cb_tbills_choose_student(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id = callback.data.split(":", 2)
    if group_id not in await _own_group_ids(user, teacher_group_repo):
        await callback.answer("Это не ваша группа", show_alert=True)
        return
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
            "В группе нет учеников.", reply_markup=kb_back(f"tblp:{period}"),
        )
        await callback.answer()
        return
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="📨 Отправить счета всей группе",
            callback_data=f"tblg_send:{period}:{group_id}",
        )],
    ]
    rows += [
        [InlineKeyboardButton(text=s.name, callback_data=f"tbls:{period}:{group_id}:{s.student_id}")]
        for s in students
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"tblp:{period}")])
    await callback.message.edit_text(
        f"<b>{display_period(period)} — {group.name}</b>\n"
        "Отправить счета всей группе или выберите ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Счёт ученика ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tbls:"))
async def cb_tbills_show(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    payment_repo: PaymentRepository, payment_service: PaymentService,
) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id, student_id = callback.data.split(":", 3)
    if group_id not in await _own_group_ids(user, teacher_group_repo):
        await callback.answer("Это не ваша группа", show_alert=True)
        return
    if student_id not in await student_group_repo.get_students_for_group(group_id):
        await callback.answer("Ученик не в этой группе", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    back_cb = f"tblg:{period}:{group_id}"
    bills = await payment_service.compute_bills_for_student_period(student_id, period)
    if not bills:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period)} нет занятий к оплате.",
            reply_markup=kb_back(back_cb),
        )
        await callback.answer()
        return

    payments = await payment_repo.get_by_student_and_period(student_id, period)
    lines = _bill_detail_lines(student.name, period, bills, payments)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📤 Отправить родителю",
                callback_data=f"tbls_send:{student_id}:{period}:{group_id}",
            )],
            [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
        ]),
    )
    await callback.answer()


# ─── Отправка ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tbls_send:"))
async def cb_tbills_send(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository,
    student_repo: StudentRepository, group_repo: GroupRepository,
    student_group_repo: StudentGroupRepository,
    payment_service: PaymentService, client_repo: ClientRepository,
) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period, group_id = callback.data.split(":", 3)
    if group_id not in await _own_group_ids(user, teacher_group_repo):
        await callback.answer("Это не ваша группа", show_alert=True)
        return

    lock_key = f"{student_id}:{period}"
    if lock_key in _sending_in_progress:
        await callback.answer("Отправка уже выполняется", show_alert=True)
        return
    _sending_in_progress.add(lock_key)
    try:
        student = await student_repo.get_by_id(student_id)
        if not student:
            await callback.answer("Ученик не найден", show_alert=True)
            return
        bills = await payment_service.compute_bills_for_student_period(student_id, period)
        if not bills:
            await callback.answer("В счёте нет занятий", show_alert=True)
            return

        group_names = await _student_group_names(student_id, student_group_repo, group_repo)
        back_cb = f"tblg:{period}:{group_id}"
        total_rec, sent_to, sent_invoices = await _send_bill_to_parents(
            callback, student, period, bills, group_names, payment_service, client_repo,
        )

        if total_rec == 0:
            await callback.message.edit_text(
                "📤 <i>Родитель не привязан к ученику — счёт отправить некому. "
                "Сообщите администратору.</i>",
                reply_markup=kb_back(back_cb),
            )
            await callback.answer()
            return
        if sent_to == 0:
            await callback.message.edit_text(
                "❌ Не удалось отправить — родитель заблокировал бота или не запускал /start.",
                reply_markup=kb_back(back_cb),
            )
            await callback.answer()
            return

        status_line = f"✅ Счёт отправлен родителю ({sent_to} из {total_rec})"
        if sent_invoices:
            status_line += f" + {sent_invoices} кнопок оплаты"
        logger.info(
            "Педагог %s отправил счёт student=%s period=%s recipients=%s sent=%s",
            user.teacher_id, student_id, period, total_rec, sent_to,
        )
        await callback.message.edit_text(
            status_line,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К ученикам", callback_data=back_cb)],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
    finally:
        _sending_in_progress.discard(lock_key)


@router.callback_query(F.data.startswith("tblg_send:"))
async def cb_tbills_group_send(
    callback: CallbackQuery, user: User | None,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    payment_service: PaymentService, client_repo: ClientRepository,
) -> None:
    if not can_teacher_bill(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, period, group_id = callback.data.split(":", 2)
    if group_id not in await _own_group_ids(user, teacher_group_repo):
        await callback.answer("Это не ваша группа", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    lock_key = f"{group_id}:{period}"
    if lock_key in _group_sending:
        await callback.answer("Рассылка уже выполняется", show_alert=True)
        return
    _group_sending.add(lock_key)
    try:
        member_ids = set(await student_group_repo.get_students_for_group(group_id))
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
        logger.info(
            "Педагог %s разослал счета group=%s period=%s sent=%s",
            user.teacher_id, group_id, period, sent_students,
        )
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К ученикам", callback_data=f"tblg:{period}:{group_id}")],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="teacher:menu")],
            ]),
        )
        await callback.answer()
    finally:
        _group_sending.discard(lock_key)
