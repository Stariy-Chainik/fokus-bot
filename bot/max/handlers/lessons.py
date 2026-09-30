"""MAX: занятия ребёнка по месяцам — расписание без денег, как в кабинете родителя."""
from __future__ import annotations

from maxapi import F
from maxapi.types import MessageCallback

from bot.api.student_lessons import month_lessons
from bot.screens.parent_lessons import child_select_screen, lessons_month_screen
from bot.services.parent_views import history_hidden
from bot.utils.dates import current_period
from ..render import edit_screen, alert
from . import router
from ._common import require_parent


def _shift(period: str, months: int) -> str:
    y, m = int(period[:4]), int(period[5:7]) + months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


async def _show(event, students: list, student, period: str, deps: dict) -> None:
    d = await month_lessons(deps, student.student_id, period, money=False)
    items = [x for x in d["lessons"] if x["attended"]]     # только отмеченные занятия, как в кабинете
    prev_ym = _shift(period, -1)
    next_ym = _shift(period, 1)
    await edit_screen(event, *lessons_month_screen(
        student.name, student.student_id, period, items,
        None if history_hidden(prev_ym) else prev_ym,
        next_ym if next_ym <= current_period() else None,
        several=len(students) > 1,
    ))


@router.message_callback(F.callback.payload.startswith("client:lessons"))
async def on_lessons(event: MessageCallback, max_uid, student_repo, payment_service, lesson_repo,
                     teacher_repo, group_repo, student_group_repo):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    parts = event.callback.payload.split(":")
    period = parts[2] if len(parts) > 2 else current_period()
    if len(students) > 1:
        await edit_screen(event, *child_select_screen(students, period))
        return
    deps = {"payment_service": payment_service, "lesson_repo": lesson_repo, "teacher_repo": teacher_repo,
            "group_repo": group_repo, "student_group_repo": student_group_repo}
    await _show(event, students, students[0], period, deps)


@router.message_callback(F.callback.payload.startswith("mxl:"))   # parent_lessons.LESSONS
async def on_lessons_month(event: MessageCallback, max_uid, student_repo, payment_service, lesson_repo,
                           teacher_repo, group_repo, student_group_repo):
    _, student_id, period = event.callback.payload.split(":", 2)
    if history_hidden(period):
        await alert(event, "Занятия показываются с сентября 2026")
        return
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    student = next((s for s in students if s.student_id == student_id), None)
    if student is None:
        await alert(event, "Ученик не найден")
        return
    deps = {"payment_service": payment_service, "lesson_repo": lesson_repo, "teacher_repo": teacher_repo,
            "group_repo": group_repo, "student_group_repo": student_group_repo}
    await _show(event, students, student, period, deps)
