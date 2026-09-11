"""«📜 История оплат»: ученик (поиск по фамилии) → месяцы → оплаты месяца.

Источник — лист student_period_payments: по каждому счёту (педагог/абонемент) видно
сумму, дату и способ подтверждения: ЮКасса (СБП онлайн, confirmed_by = 0) или
вручную администратором (чек / наличные / кнопка «Подтвердить оплату»).
"""
from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import (
    StudentRepository, PaymentRepository, BranchRepository, GroupRepository, StudentGroupRepository,
)
from bot.states import PaymentHistoryStates
from bot.keyboards.admin import kb_back
from bot.utils.dates import display_period, month_name_ru
from bot.handlers.access import is_admin as _is_admin

logger = logging.getLogger(__name__)
router = Router(name="admin_payment_history")

_PROMPT = "📜 <b>История оплат</b>\n\nВведите фамилию ученика:"


def _period_label(period_month: str) -> str:
    year, month = period_month.split("-")
    return f"{month_name_ru(int(month))} {year}"


def _method(p) -> str:
    if p.confirmed_by_tg_id == 0:
        return "СБП онлайн (ЮКасса)"
    return "вручную (чек / наличные / админ)"


def _fmt_date(value: str | None) -> str:
    if not value:
        return ""
    d = value[:10]
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) == 10 else d


@router.callback_query(F.data == "admin:payhist")
async def cb_payhist_start(
    callback: CallbackQuery, user: User | None, state: FSMContext, branch_repo: BranchRepository,
) -> None:
    """Старт: филиалы кнопками или ввод фамилии (состояние поиска включено сразу)."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(PaymentHistoryStates.searching)
    branches = sorted(await branch_repo.get_all(), key=lambda b: b.name)
    rows = [[InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"payhist_br:{b.branch_id}")] for b in branches]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    await callback.message.edit_text(
        "📜 <b>История оплат</b>\n\nВыберите филиал — или просто введите фамилию ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payhist_br:"))
async def cb_payhist_branch(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    branch_repo: BranchRepository, group_repo: GroupRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    branch_id = callback.data.split(":", 1)[1]
    branch = await branch_repo.get_by_id(branch_id)
    groups = sorted(await group_repo.get_by_branch(branch_id), key=lambda g: g.name)
    rows = [[InlineKeyboardButton(text=g.name, callback_data=f"payhist_g:{g.group_id}")] for g in groups]
    rows.append([InlineKeyboardButton(text="« Филиалы", callback_data="admin:payhist")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    await callback.message.edit_text(
        f"📜 <b>История оплат — {branch.name if branch else branch_id}</b>\n\nВыберите группу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payhist_g:"))
async def cb_payhist_group(
    callback: CallbackQuery, user: User | None,
    group_repo: GroupRepository, student_repo: StudentRepository,
    student_group_repo: StudentGroupRepository, payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    group = await group_repo.get_by_id(group_id)
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    students = sorted((s for s in await student_repo.get_all() if s.student_id in member_ids), key=lambda s: s.name.lower())
    paid_by_student: dict[str, int] = {}
    pending_by_student: dict[str, int] = {}
    for p in await payment_repo.get_all():
        if p.student_id in member_ids:
            if p.status == PaymentStatus.PAID:
                paid_by_student[p.student_id] = paid_by_student.get(p.student_id, 0) + p.total_amount
            else:
                pending_by_student[p.student_id] = pending_by_student.get(p.student_id, 0) + p.total_amount
    rows = []
    for s in students:
        mark = "⏳ " if pending_by_student.get(s.student_id) else ("✅ " if paid_by_student.get(s.student_id) else "")
        rows.append([InlineKeyboardButton(text=f"{mark}{s.name}", callback_data=f"payhist_stu:{s.student_id}")])
    rows.append([InlineKeyboardButton(text="« Группы", callback_data=f"payhist_br:{group.branch_id if group else ''}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    await callback.message.edit_text(
        f"📜 <b>{group.name if group else group_id}</b>\n⏳ — есть неоплаченные счета, ✅ — всё оплачено.\n\nВыберите ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.message(PaymentHistoryStates.searching, F.text)
async def on_payhist_search(
    message: Message, user: User | None, state: FSMContext, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        return
    query = (message.text or "").strip().lower()
    if len(query) < 2:
        await message.answer("Введите минимум 2 буквы фамилии:", reply_markup=kb_back("admin:menu"))
        return
    matches = sorted(
        (s for s in await student_repo.get_all() if s.name.lower().startswith(query)),
        key=lambda s: s.name.lower(),
    )
    if not matches:
        await message.answer(f"Ученик с фамилией <b>{message.text.strip()}</b> не найден. Попробуйте ещё раз:",
                             reply_markup=kb_back("admin:menu"))
        return
    await state.clear()
    rows = [[InlineKeyboardButton(text=s.name, callback_data=f"payhist_stu:{s.student_id}")] for s in matches[:15]]
    rows.append([InlineKeyboardButton(text="« Филиалы / поиск", callback_data="admin:payhist")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    await message.answer("Выберите ученика:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _render_periods(callback: CallbackQuery, student, payment_repo: PaymentRepository) -> None:
    pays = [p for p in await payment_repo.get_all() if p.student_id == student.student_id]
    by_period: dict[str, list] = {}
    for p in pays:
        by_period.setdefault(p.period_month, []).append(p)
    rows = []
    lines = [f"📜 <b>История оплат — {student.name}</b>"]
    if not by_period:
        lines.append("\nСчетов и оплат пока нет.")
    for period in sorted(by_period, reverse=True):
        items = by_period[period]
        paid = sum(p.total_amount for p in items if p.status == PaymentStatus.PAID)
        pending = sum(p.total_amount for p in items if p.status != PaymentStatus.PAID)
        label = f"{_period_label(period)} — {paid} ₽" + (f" · ожидает {pending} ₽" if pending else "")
        icon = "✅" if paid and not pending else ("⏳" if pending else "•")
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"payhist_p:{student.student_id}:{period}")])
    total_paid = sum(p.total_amount for p in pays if p.status == PaymentStatus.PAID)
    if pays:
        lines.append(f"\nВсего оплачено: <b>{total_paid} ₽</b>\nВыберите месяц:")
    rows.append([InlineKeyboardButton(text="🔍 Другой ученик", callback_data="admin:payhist")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("payhist_stu:"))
async def cb_payhist_student(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    student_repo: StudentRepository, payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    student = await student_repo.get_by_id(callback.data.split(":", 1)[1])
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await _render_periods(callback, student, payment_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("payhist_p:"))
async def cb_payhist_period(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, payment_repo: PaymentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    pays = [p for p in await payment_repo.get_by_student_and_period(student_id, period)]
    paid = [p for p in pays if p.status == PaymentStatus.PAID]
    pending = [p for p in pays if p.status != PaymentStatus.PAID]
    lines = [f"📜 <b>{student.name} — {_period_label(period)}</b>"]
    if paid:
        lines.append("\n<b>Оплачено:</b>")
        for p in sorted(paid, key=lambda p: (p.paid_at or "", p.teacher_name or "")):
            lines.append(f"✅ {_fmt_date(p.paid_at)} · {p.teacher_name or p.teacher_id} — <b>{p.total_amount} ₽</b>\n"
                         f"    {_method(p)}" + (f" · {p.comment}" if p.comment else ""))
        lines.append(f"Итого оплачено: <b>{sum(p.total_amount for p in paid)} ₽</b>")
    if pending:
        lines.append("\n<b>Ожидает оплаты:</b>")
        for p in sorted(pending, key=lambda p: p.teacher_name or ""):
            lines.append(f"⏳ {p.teacher_name or p.teacher_id} — {p.total_amount} ₽")
        lines.append(f"Итого к оплате: <b>{sum(p.total_amount for p in pending)} ₽</b>")
    if not pays:
        lines.append("\nСчетов за этот месяц нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« К месяцам", callback_data=f"payhist_stu:{student_id}")],
        [InlineKeyboardButton(text="🔍 Другой ученик", callback_data="admin:payhist")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()
