from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.models import GroupBillingMode, User
from bot.repositories import (
    GroupRepository,
    LessonRepository,
    StudentGroupRepository,
    StudentRepository,
    TeacherGroupRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.utils import AttendeeEntry, attendee_ids, parse_attendees, serialize_attendees

from ._base import _is_teacher_or_admin, _submitted_periods, router


@router.callback_query(F.data.startswith("lesson_guest_list:"))
async def cb_lesson_guest_list(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    present_ids = set(attendee_ids(lesson.attendees)) if lesson.attendees else set()
    group_ids = set(
        await teacher_group_repo.get_groups_for_teacher(lesson.teacher_id)
    )
    all_students = await student_repo.get_all()
    candidates_by_id: dict[str, object] = {}
    for group_id in sorted(group_ids):
        member_ids = set(
            await student_group_repo.get_students_for_group(group_id)
        )
        for student in all_students:
            if student.student_id in member_ids and student.student_id not in present_ids:
                candidates_by_id[student.student_id] = student

    candidates = sorted(candidates_by_id.values(), key=lambda student: student.name)
    if not candidates:
        await callback.answer("Все ученики уже отмечены.", show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=student.name,
            callback_data=f"lesson_guest_pick:{lesson_id}:{student.student_id}",
        )]
        for student in candidates
    ]
    rows.append([InlineKeyboardButton(
        text="« Назад",
        callback_data=f"lesson_detail:{lesson_id}",
    )])
    await callback.message.edit_text(
        f"<b>Выберите ученика</b> (уже отмечено: {len(present_ids)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_guest_pick:"))
async def cb_lesson_guest_pick(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    state: FSMContext,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, lesson_id, student_id = callback.data.split(":", 2)
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    if not user.is_admin:
        periods = await _submitted_periods(lesson.teacher_id, submission_repo)
        if lesson.date[:7] in periods:
            await callback.answer("🔒 Период сдан.", show_alert=True)
            return

    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    group = await group_repo.get_by_id(lesson.group_id) if lesson.group_id else None
    amount = (
        group.price_full
        if group and group.billing_mode == GroupBillingMode.PER_VISIT
        else 0
    )
    new_entry = AttendeeEntry(
        student_id=student_id,
        duration_min=lesson.duration_min,
        amount=amount,
    )

    existing = parse_attendees(
        lesson.attendees or "",
        default_duration=lesson.duration_min,
    )
    if any(entry.student_id == student_id for entry in existing):
        await callback.answer("Этот ученик уже отмечен.", show_alert=True)
        return

    existing.append(new_entry)
    new_attendees = serialize_attendees(existing)
    await lesson_repo.update_attendees(lesson_id, new_attendees)

    await callback.answer(f"✅ {student.name} добавлен(а).")
    lesson.attendees = new_attendees
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="« К занятию",
                callback_data=f"lesson_detail:{lesson_id}",
            ),
        ]]),
    )
