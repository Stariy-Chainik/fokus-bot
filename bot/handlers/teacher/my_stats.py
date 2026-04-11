from __future__ import annotations
import logging
from collections import defaultdict
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import LessonRepository, BillingRepository, PaymentRepository
from bot.keyboards.teacher import kb_teacher_menu
from bot.utils.dates import display_period

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_stats")


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


def _period_buttons() -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"stats_period:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "teacher:my_stats")
async def cb_my_stats(callback: CallbackQuery, user: User | None) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Выберите период:", reply_markup=_period_buttons())
    await callback.answer()


@router.callback_query(F.data.startswith("stats_period:"))
async def cb_stats_period(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    billing_repo: BillingRepository,
    payment_repo: PaymentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    period_month = callback.data.split(":", 1)[1]
    teacher_id = user.teacher_id

    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    total_earned = sum(ls.earned for ls in lessons)
    group_count = sum(1 for ls in lessons if ls.type.value == "group")
    ind_count = len(lessons) - group_count

    billing_rows = await billing_repo.get_by_teacher_and_period(teacher_id, period_month)

    # Статус оплаты по каждому ученику (не повторяя)
    students_amount: dict[str, int] = defaultdict(int)
    students_paid: dict[str, bool] = {}
    for b in billing_rows:
        students_amount[b.student_id] += b.amount
        if b.student_id not in students_paid:
            payment = await payment_repo.get_by_student_and_period(b.student_id, period_month)
            students_paid[b.student_id] = payment is not None and payment.status.value == "paid"

    lines = [
        f"Статистика за {display_period(period_month)}:",
        "",
        f"Занятий: {len(lessons)} (груп.: {group_count}, инд.: {ind_count})",
        f"Начислено: {total_earned} руб.",
    ]
    if students_amount:
        lines.append("")
        lines.append("Статусы оплат:")
        seen_names: dict[str, str] = {}
        for b in billing_rows:
            if b.student_id not in seen_names:
                seen_names[b.student_id] = b.student_name
        for sid, name in seen_names.items():
            icon = "✅" if students_paid.get(sid) else "⏳"
            lines.append(f"  {icon} {name}: {students_amount[sid]} руб.")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="teacher:my_stats")]
        ]),
    )
    await callback.answer()
