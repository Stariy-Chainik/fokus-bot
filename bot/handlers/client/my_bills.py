from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, LabeledPrice

from dateutil.relativedelta import relativedelta  # type: ignore

from bot.models.enums import PaymentStatus
from bot.repositories import StudentRepository, LessonRepository, TeacherRepository
from bot.services import PaymentService
from bot.services.billing_service import build_billing_rows
from bot.utils.dates import display_period, format_date_display
from bot.keyboards.client import kb_client_menu, kb_client_student_select, kb_bills_list, kb_bill_detail
from config.settings import settings

logger = logging.getLogger(__name__)
router = Router(name="client_bills")

_MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _period_label(period_month: str) -> str:
    year, month = period_month.split("-")
    return f"{_MONTHS_RU[int(month)]} {year}"


async def _show_bills(
    callback: CallbackQuery,
    students_all: list,
    student_id: str,
    payment_service: PaymentService,
) -> None:
    if student_id != "all":
        students = [s for s in students_all if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = students_all

    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]

    period_rows: list[tuple[str, str, str]] = []
    for period_month in periods:
        total = 0
        for student in students:
            bills = await payment_service.compute_bills_for_student_period(
                student.student_id, period_month,
            )
            total += sum(agg["total"] for agg in bills.values())

        label = _period_label(period_month)
        is_current = period_month == periods[0]

        if is_current:
            icon = "📅"
            suffix = f" — {total} руб. (текущий)" if total else " — нет занятий"
        else:
            paid = False
            for student in students:
                invoices = await payment_service.get_or_create_invoices_for_student_period(
                    student, period_month,
                )
                if invoices and all(inv.status == PaymentStatus.PAID for inv in invoices):
                    paid = True
                    break
            if total == 0:
                continue
            icon = "✅" if paid else "⏳"
            suffix = f" — {total} руб."

        period_rows.append((period_month, f"{label}{suffix}", icon))

    if not period_rows:
        await callback.message.edit_text(
            "📋 Занятий за последние 6 месяцев не найдено.",
            reply_markup=kb_client_menu(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "<b>📋 Счета</b>",
        reply_markup=kb_bills_list(period_rows, student_id),
    )
    await callback.answer()


@router.callback_query(F.data == "client:my_bills")
async def cb_my_bills(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if len(students) > 1:
        await callback.message.edit_text(
            "Выберите ученика:",
            reply_markup=kb_client_student_select(students, "bills"),
        )
        await callback.answer()
        return

    await _show_bills(callback, students, students[0].student_id, payment_service)


@router.callback_query(F.data.startswith("cl_bills_stu:"))
async def cb_cl_bills_stu(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_bills(callback, students, student_id, payment_service)


@router.callback_query(F.data.startswith("client_bill:"))
async def cb_bill_detail(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # client_bill:{student_id}:{period_month}
    _, student_id, period_month = callback.data.split(":", 2)

    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not all_students:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if student_id != "all":
        students = [s for s in all_students if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = all_students

    lines = [f"<b>📋 {_period_label(period_month)}</b>\n"]
    grand_total = 0
    payment_ids: list[str] = []
    all_paid = True
    has_invoices = False

    for student in students:
        bills = await payment_service.compute_bills_for_student_period(
            student.student_id, period_month,
        )
        if not bills:
            continue

        lines.append(f"<b>{student.name}:</b>")

        for teacher_id, agg in bills.items():
            teacher_name = agg["name"] or teacher_id
            lines.append(f"<b>Педагог: {teacher_name}</b>")
            for item in sorted(agg["items"], key=lambda b: b.date):
                lines.append(f"  {format_date_display(item.date)}  {item.duration_min} мин  — {item.amount} руб.")
            lines.append(f"  <i>Итого: {agg['total']} руб.</i>\n")
            grand_total += agg["total"]

        invoices = await payment_service.get_or_create_invoices_for_student_period(
            student, period_month,
        )
        for inv in invoices:
            has_invoices = True
            payment_ids.append(inv.payment_id)
            if inv.status != PaymentStatus.PAID:
                all_paid = False

    if grand_total == 0:
        await callback.message.edit_text(
            f"📋 {_period_label(period_month)}\n\nЗанятий не найдено.",
            reply_markup=kb_bill_detail([], False, period_month, student_id),
        )
        await callback.answer()
        return

    lines.append(f"<b>Итого за месяц: {grand_total} руб.</b>")
    if has_invoices:
        lines.append("✅ Оплачен" if all_paid else "⏳ Ожидает оплаты")

    can_pay = (
        has_invoices
        and not all_paid
        and bool(settings.payment_provider_token)
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_bill_detail(payment_ids if can_pay else [], can_pay, period_month, student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_pay:"))
async def cb_client_pay(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not settings.payment_provider_token:
        await callback.answer("Онлайн-оплата временно недоступна", show_alert=True)
        return

    payment_id = callback.data.split(":", 1)[1]
    payment_repo = payment_service._payment_repo
    payment = await payment_repo.get_by_id(payment_id)
    if not payment:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    if payment.status == PaymentStatus.PAID:
        await callback.answer("Этот счёт уже оплачен", show_alert=True)
        return

    period_label = _period_label(payment.period_month)
    teacher_name = payment.teacher_name or "педагог"

    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Занятия {period_label}",
        description=f"Педагог: {teacher_name}",
        payload=payment.payment_id,
        provider_token=settings.payment_provider_token,
        currency="RUB",
        prices=[LabeledPrice(label="Обучение", amount=payment.total_amount * 100)],
    )
    await callback.answer()
