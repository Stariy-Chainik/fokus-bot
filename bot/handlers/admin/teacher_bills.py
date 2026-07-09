"""Админ: «🧾 Счета по педагогу» — выставление счетов ученикам групп педагога.

Флоу: выбрать педагога → его группы (за период) → ученик → отправить родителю,
либо разослать счета всем родителям группы одной кнопкой.

Счёт считается как у обычного счёта ученика (все педагоги ученика, не только
выбранный) — тот же текст, что в admin/bills. Выбор педагога здесь — лишь способ
навигации по его группам. Заменяет прежний спец-раздел Контаревой (спецроли убраны).
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from bot.models import User
from bot.repositories import (
    ClientRepository, GroupRepository, StudentGroupRepository,
    StudentRepository, TeacherGroupRepository, TeacherRepository,
)
from bot.services import PaymentService
from bot.utils.bill_format import build_bill_text
from bot.utils.dates import display_period
from bot.utils.locks import InProgressGuard
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router(name="admin_teacher_bills")

from bot.handlers.access import is_admin as _is_admin

_sending = InProgressGuard()
_group_sending = InProgressGuard()


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


def _periods(n: int = 3) -> list[str]:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    return [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(n)]


async def _teacher_groups(
    teacher_id: str, teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> list:
    gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    return sorted(
        [g for g in await group_repo.get_all() if g.group_id in gids],
        key=lambda g: g.name,
    )


# ─── Вход: выбор педагога ────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:teacher_bills")
async def cb_tbills_pick_teacher(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = sorted(await teacher_repo.get_all(), key=lambda t: t.name)
    rows = [
        [InlineKeyboardButton(text=f"👨‍🏫 {t.name}", callback_data=f"atb:t:{t.teacher_id}")]
        for t in teachers
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    await callback.message.edit_text(
        "<b>🧾 Счета по педагогу</b>\n\nВыберите педагога:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Группы педагога за период ───────────────────────────────────────────────

async def _show_groups(
    callback: CallbackQuery, teacher_id: str, period: str,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    groups = await _teacher_groups(teacher_id, teacher_group_repo, group_repo)
    if not groups:
        await callback.message.edit_text(
            f"У педагога <b>{teacher.name}</b> нет прикреплённых групп.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К педагогам", callback_data="admin:teacher_bills")],
            ]),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"💃 {g.name}", callback_data=f"atb:g:{teacher_id}:{period}:{g.group_id}",
        )]
        for g in groups
    ]
    rows.append([InlineKeyboardButton(
        text="🗓 Сменить период", callback_data=f"atb:pp:{teacher_id}:{period}",
    )])
    rows.append([InlineKeyboardButton(text="« К педагогам", callback_data="admin:teacher_bills")])
    await callback.message.edit_text(
        f"<b>🧾 {teacher.name} — {display_period(period)}</b>\n\nВыберите группу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("atb:t:"))
async def cb_tbills_teacher(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 2)[2]
    await _show_groups(callback, teacher_id, _current_period(), teacher_repo, teacher_group_repo, group_repo)


@router.callback_query(F.data.startswith("atb:p:"))
async def cb_tbills_period(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, period = callback.data.split(":", 3)
    await _show_groups(callback, teacher_id, period, teacher_repo, teacher_group_repo, group_repo)


@router.callback_query(F.data.startswith("atb:pp:"))
async def cb_tbills_period_picker(
    callback: CallbackQuery, user: User | None,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, active = callback.data.split(":", 3)
    rows = []
    for p in _periods(3):
        mark = "✅ " if p == active else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{display_period(p)}", callback_data=f"atb:p:{teacher_id}:{p}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"atb:p:{teacher_id}:{active}")])
    await callback.message.edit_text(
        "<b>Выберите период:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Ученики группы + «отправить всем» ───────────────────────────────────────

@router.callback_query(F.data.startswith("atb:g:"))
async def cb_tbills_group(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, period, gid = callback.data.split(":", 4)
    group = await group_repo.get_by_id(gid)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    member_ids = set(await student_group_repo.get_students_for_group(gid))
    students = sorted(
        [s for s in await student_repo.get_all() if s.student_id in member_ids],
        key=lambda s: s.name,
    )
    if not students:
        await callback.message.edit_text(
            f"В группе «{group.name}» нет учеников.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"atb:p:{teacher_id}:{period}")],
            ]),
        )
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    has_any_bills = False
    for s in students:
        bills = await payment_service.compute_bills_for_student_period(s.student_id, period)
        total = sum(agg["total"] for agg in bills.values())
        if total > 0:
            has_any_bills = True
            label = f"{s.name} — {total}₽"
        else:
            label = f"{s.name} — нет занятий"
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f"atb:s:{teacher_id}:{period}:{gid}:{s.student_id}",
        )])

    if has_any_bills:
        rows.append([InlineKeyboardButton(
            text="📨 Отправить счета всем родителям",
            callback_data=f"atb:all:{teacher_id}:{period}:{gid}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"atb:p:{teacher_id}:{period}")])

    await callback.message.edit_text(
        f"<b>🧾 {group.name} — {display_period(period)}</b>\n\nВыберите ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Карточка счёта ученика ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("atb:s:"))
async def cb_tbills_student(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository, payment_service: PaymentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, period, gid, sid = callback.data.split(":", 5)
    student = await student_repo.get_by_id(sid)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    bills = await payment_service.compute_bills_for_student_period(sid, period)
    if not bills:
        await callback.message.edit_text(
            f"У {student.name} за {display_period(period)} нет занятий к оплате.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"atb:g:{teacher_id}:{period}:{gid}")],
            ]),
        )
        await callback.answer()
        return

    gids = await student_group_repo.get_groups_for_student(sid)
    group_names: list[str] = []
    for g_id in gids:
        g = await group_repo.get_by_id(g_id)
        if g:
            group_names.append(g.name)

    bill_text, _grand_total = build_bill_text(student.name, group_names, period, bills)

    rows = [
        [InlineKeyboardButton(
            text="📤 Отправить родителю",
            callback_data=f"atb:snd:{teacher_id}:{period}:{gid}:{sid}",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data=f"atb:g:{teacher_id}:{period}:{gid}")],
    ]
    await callback.message.edit_text(
        bill_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ─── Отправка родителям (общий helper) ───────────────────────────────────────

async def _send_bill_to_parents(
    callback: CallbackQuery, student, period: str, bills: dict,
    group_names: list[str],
    payment_service: PaymentService, client_repo: ClientRepository,
) -> tuple[int, int, int]:
    """Возвращает (recipients_total, sent_to, sent_invoices)."""
    invoices = await payment_service.get_or_create_invoices_for_student_period(student, period)
    bill_text, _ = build_bill_text(student.name, group_names, period, bills)

    client = await client_repo.get_by_id(student.client_id) if student.client_id else None
    recipients: list[int] = []
    if client and client.tg_id:
        recipients.append(client.tg_id)
    for pid in (student.parent_tg_ids or []):
        if pid not in recipients:
            recipients.append(pid)

    sent_to = 0
    sent_invoices = 0
    for tg_id in recipients:
        try:
            await callback.bot.send_message(tg_id, bill_text)
            sent_to += 1
        except Exception as exc:
            logger.error("Ошибка отправки родителю tg_id=%s: %s", tg_id, exc)
            continue
        if settings.payment_provider_token:
            for p in invoices:
                if p.status.value != "paid":
                    try:
                        await callback.bot.send_invoice(
                            chat_id=tg_id,
                            title=f"Занятия {display_period(period)}",
                            description=f"Педагог: {p.teacher_name or '—'}",
                            payload=p.payment_id,
                            provider_token=settings.payment_provider_token,
                            currency="RUB",
                            prices=[LabeledPrice(label="Обучение", amount=p.total_amount * 100)],
                        )
                        sent_invoices += 1
                    except Exception as exc:
                        logger.error("Ошибка инвойса %s: %s", p.payment_id, exc)
    return len(recipients), sent_to, sent_invoices


@router.callback_query(F.data.startswith("atb:snd:"))
async def cb_tbills_send_one(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
    payment_service: PaymentService, client_repo: ClientRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, period, gid, sid = callback.data.split(":", 5)
    back_cb = f"atb:g:{teacher_id}:{period}:{gid}"

    lock_key = f"{sid}:{period}"
    if lock_key in _sending:
        await callback.answer("Отправка уже выполняется", show_alert=True)
        return
    _sending.add(lock_key)
    try:
        student = await student_repo.get_by_id(sid)
        if not student:
            await callback.answer("Ученик не найден", show_alert=True)
            return
        bills = await payment_service.compute_bills_for_student_period(sid, period)
        if not bills:
            await callback.answer("Нет занятий в счёте", show_alert=True)
            return

        gids = await student_group_repo.get_groups_for_student(sid)
        group_names: list[str] = []
        for g_id in gids:
            g = await group_repo.get_by_id(g_id)
            if g:
                group_names.append(g.name)

        total_rec, sent_to, sent_invoices = await _send_bill_to_parents(
            callback, student, period, bills, group_names, payment_service, client_repo,
        )

        if total_rec == 0:
            await callback.message.edit_text(
                "❌ У ученика не привязан родитель (нет Telegram).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
                ]),
            )
            return
        if sent_to == 0:
            await callback.message.edit_text(
                "❌ Не удалось отправить — родитель заблокировал бота или не запускал /start.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
                ]),
            )
            return
        status = f"✅ Счёт отправлен ({sent_to} из {total_rec})"
        if sent_invoices:
            status += f" + {sent_invoices} кнопок оплаты"
        logger.info("Admin teacher-bill sent teacher=%s student=%s period=%s sent=%s",
                    teacher_id, sid, period, sent_to)
        await callback.message.edit_text(
            status,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К ученикам", callback_data=back_cb)],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")],
            ]),
        )
        await callback.answer()
    finally:
        _sending.discard(lock_key)


# ─── Массовая рассылка по группе ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("atb:all:"))
async def cb_tbills_send_all(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, student_group_repo: StudentGroupRepository,
    group_repo: GroupRepository,
    payment_service: PaymentService, client_repo: ClientRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, teacher_id, period, gid = callback.data.split(":", 4)
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
            gids = await student_group_repo.get_groups_for_student(s.student_id)
            group_names: list[str] = []
            for g_id in gids:
                g = await group_repo.get_by_id(g_id)
                if g:
                    group_names.append(g.name)
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

        back_cb = f"atb:g:{teacher_id}:{period}:{gid}"
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
        logger.info("Admin teacher-group bill sent teacher=%s group=%s period=%s sent=%s",
                    teacher_id, gid, period, sent_students)
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
