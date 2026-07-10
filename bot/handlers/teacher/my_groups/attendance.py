from __future__ import annotations
"""Педагог: «Группы» — просмотр своих групп, управление составом."""
import logging
from collections import defaultdict

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import (
    StudentRepository, GroupRepository, TeacherGroupRepository, StudentGroupRepository,
    LessonRepository,
)
from bot.utils.attendees import attendee_ids
from bot.utils.dates import month_name_ru, last_periods

logger = logging.getLogger(__name__)


from bot.handlers.access import is_teacher as _is_teacher



from ._base import router, _owns_group


# ─── Посещаемость группы ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("t_grp_attendance:"))
async def cb_t_grp_attendance(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    teacher_group_repo: TeacherGroupRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = callback.data.split(":", 1)[1]
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    months = last_periods(3)
    rows = []
    for ym in months:
        y, m = ym.split("-")
        rows.append([InlineKeyboardButton(
            text=f"{month_name_ru(int(m))} {y}",
            callback_data=f"t_grp_att_m:{group_id}:{ym}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_group_card:{group_id}")])

    await callback.message.edit_text(
        f"<b>📊 Посещаемость — {group.name}</b>\n\nВыберите месяц:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_grp_att_m:"))
async def cb_t_grp_att_month(
    callback: CallbackQuery,
    user: User | None,
    group_repo: GroupRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, group_id, period_month = callback.data.split(":", 2)
    if not await _owns_group(user.teacher_id, group_id, teacher_group_repo):
        await callback.answer("Эта группа не ваша", show_alert=True)
        return
    group = await group_repo.get_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    all_students = await student_repo.get_all()
    members = {s.student_id: s.name for s in all_students if s.student_id in member_ids}

    all_lessons = await lesson_repo.get_by_teacher_and_period(user.teacher_id, period_month)
    group_lessons = sorted(
        [ls for ls in all_lessons if ls.type == LessonType.GROUP and ls.group_id == group_id],
        key=lambda ls: ls.date,
    )

    attendance: dict[str, list[str]] = defaultdict(list)
    for ls in group_lessons:
        for sid in attendee_ids(ls.attendees):
            if sid in members:
                attendance[sid].append(ls.date)

    y, m = period_month.split("-")
    month_label = f"{month_name_ru(int(m))} {y}"

    lines = [
        f"<b>📊 {group.name}</b>",
        f"{month_label} · занятий: {len(group_lessons)}",
        "",
    ]

    sorted_ids = sorted(members.keys(), key=lambda sid: -len(attendance.get(sid, [])))
    for sid in sorted_ids:
        name = members[sid]
        dates = attendance.get(sid, [])
        if dates:
            dates_str = ", ".join(d[8:10] + "." + d[5:7] for d in dates)
            lines.append(f"<b>{name}</b> — {len(dates)}")
            lines.append(f"  {dates_str}")
        else:
            lines.append(f"<b>{name}</b> — 0")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=f"t_grp_attendance:{group_id}")],
        ]),
    )
    await callback.answer()
