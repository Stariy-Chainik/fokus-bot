from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import TeacherRepository, LessonRepository, BillingRepository
from bot.keyboards.admin import kb_salaries_menu, kb_teacher_list, kb_back
from bot.utils.dates import display_period

logger = logging.getLogger(__name__)
router = Router(name="admin_salaries")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _period_buttons(teacher_id: str) -> InlineKeyboardMarkup:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(6)]
    buttons = [
        [InlineKeyboardButton(text=display_period(p), callback_data=f"salary_period:{teacher_id}:{p}")]
        for p in periods
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="salaries:view")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin:salaries")
async def cb_salaries_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Зарплаты педагогов:", reply_markup=kb_salaries_menu())
    await callback.answer()


@router.callback_query(F.data == "salaries:view")
async def cb_salaries_choose_teacher(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:salaries"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "Выберите педагога:", reply_markup=kb_teacher_list(teachers, "salary_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_teacher:"))
async def cb_salary_choose_period(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await callback.message.edit_text(
        f"Выберите период для педагога {teacher_id}:",
        reply_markup=_period_buttons(teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("salary_period:"))
async def cb_salary_show(
    callback: CallbackQuery,
    user: User | None,
    teacher_repo: TeacherRepository,
    lesson_repo: LessonRepository,
    billing_repo: BillingRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period_month = callback.data.split(":", 2)
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return

    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    total_earned = sum(ls.earned for ls in lessons)

    billing_rows = await billing_repo.get_by_teacher_and_period(teacher_id, period_month)
    total_billing = len(billing_rows)
    paid_billing = sum(1 for b in billing_rows if b.payment_id)
    period_closed = total_billing > 0 and paid_billing == total_billing

    lines = [
        f"Педагог: {teacher.name}",
        f"Период: {display_period(period_month)}",
        "",
        f"Занятий: {len(lessons)}",
        f"Начислено: {total_earned} руб.",
        "",
        f"Billing: {paid_billing}/{total_billing} оплачено",
        f"Период: {'✅ Закрыт' if period_closed else '⏳ Открыт'}",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=kb_back("admin:salaries"))
    await callback.answer()
