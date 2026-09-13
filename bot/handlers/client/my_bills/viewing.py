"""Родитель: список месяцев и детализация счёта (Telegram). Данные и экраны — общие с MAX."""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.repositories import StudentRepository
from bot.services import PaymentService
from bot.services.parent_views import bills_periods, bill_detail
from bot.screens.adapters import to_aiogram_markup
from bot.screens.parent_bills import student_select_screen, bills_list_screen, bill_detail_screen
from ._base import router

logger = logging.getLogger(__name__)


async def _edit(callback: CallbackQuery, screen: tuple) -> None:
    text, rows = screen
    try:
        await callback.message.edit_text(text, reply_markup=to_aiogram_markup(rows))
    except TelegramBadRequest:
        pass
    await callback.answer()


async def _show_bills(
    callback: CallbackQuery, students_all: list, student_id: str,
    payment_service: PaymentService, show_older: bool = False,
) -> None:
    if student_id != "all":
        students = [s for s in students_all if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = students_all
    period_rows = await bills_periods(students, payment_service, show_older)
    who = students[0].name if student_id != "all" and len(students) == 1 else "все дети"
    await _edit(callback, bills_list_screen(
        period_rows, student_id, who, show_older,
        show_back=len(students_all) > 1,
    ))


@router.callback_query(F.data == "client:my_bills")
async def cb_my_bills(
    callback: CallbackQuery, student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if len(students) > 1:
        await _edit(callback, student_select_screen(students, "bills"))
        return
    await _show_bills(callback, students, students[0].student_id, payment_service)


@router.callback_query(F.data.startswith("cl_bills_stu:"))
async def cb_cl_bills_stu(
    callback: CallbackQuery, student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_bills(callback, students, student_id, payment_service)


@router.callback_query(F.data.startswith("cl_bills_more:"))
async def cb_cl_bills_more(
    callback: CallbackQuery, student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_bills(callback, students, student_id, payment_service, show_older=True)


@router.callback_query(F.data.startswith("client_bill:"))
async def cb_bill_detail(
    callback: CallbackQuery, student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
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
    detail = await bill_detail(students, period_month, payment_service)
    await _edit(callback, bill_detail_screen(detail, period_month, student_id))
