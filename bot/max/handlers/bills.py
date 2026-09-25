"""MAX: список месяцев и детализация счёта (те же экраны, что в Telegram)."""
from __future__ import annotations

from maxapi import F
from maxapi.types import MessageCallback

from bot.services.parent_views import bills_periods, bill_detail, history_hidden, visible_periods
from bot.screens.parent_bills import student_select_screen, bills_list_screen, bill_detail_screen
from ..render import edit_screen, alert
from . import router
from ._common import require_parent


async def _show_bills(event, students_all: list, student_id: str, payment_service, show_older: bool = False) -> None:
    students = students_all if student_id == "all" else [s for s in students_all if s.student_id == student_id]
    if not students:
        await alert(event, "Ученик не найден")
        return
    period_rows = await bills_periods(students, payment_service, show_older)
    who = students[0].name if student_id != "all" and len(students) == 1 else "все дети"
    await edit_screen(event, *bills_list_screen(period_rows, student_id, who, show_older, show_back=len(students_all) > 1,
                                                has_older=len(visible_periods(6)) > 2))


@router.message_callback(F.callback.payload == "client:my_bills")
async def on_my_bills(event: MessageCallback, max_uid, student_repo, payment_service):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    if len(students) > 1:
        await edit_screen(event, *student_select_screen(students, "bills"))
        return
    await _show_bills(event, students, students[0].student_id, payment_service)


@router.message_callback(F.callback.payload.startswith("cl_bills_stu:"))
async def on_bills_stu(event: MessageCallback, max_uid, student_repo, payment_service):
    students = await require_parent(event, student_repo, max_uid)
    if students:
        await _show_bills(event, students, event.callback.payload.split(":", 1)[1], payment_service)


@router.message_callback(F.callback.payload.startswith("cl_bills_more:"))
async def on_bills_more(event: MessageCallback, max_uid, student_repo, payment_service):
    students = await require_parent(event, student_repo, max_uid)
    if students:
        await _show_bills(event, students, event.callback.payload.split(":", 1)[1], payment_service, show_older=True)


@router.message_callback(F.callback.payload.startswith("client_bill:"))
async def on_bill_detail(event: MessageCallback, max_uid, student_repo, payment_service):
    _, student_id, period_month = event.callback.payload.split(":", 2)
    if history_hidden(period_month):
        await alert(event, "Счета показываются с сентября 2026")
        return
    all_students = await require_parent(event, student_repo, max_uid)
    if not all_students:
        return
    students = all_students if student_id == "all" else [s for s in all_students if s.student_id == student_id]
    if not students:
        await alert(event, "Ученик не найден")
        return
    detail = await bill_detail(students, period_month, payment_service)
    await edit_screen(event, *bill_detail_screen(detail, period_month, student_id))
