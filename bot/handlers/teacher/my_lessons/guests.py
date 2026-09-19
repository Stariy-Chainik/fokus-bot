from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.filters import TeacherOrAdmin
from bot.models import Student, User
from bot.models.enums import GroupBillingMode
from bot.utils.groups import hide_service_groups
from bot.repositories import (
    GroupRepository,
    LessonRepository,
    StudentGroupRepository,
    StudentRepository,
    TeacherGroupRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.services import LessonService
from bot.utils import attendee_ids

from ._base import _submitted_periods, router


@router.callback_query(F.data.startswith("lesson_guest_list:"), TeacherOrAdmin())
async def cb_lesson_guest_list(
    callback: CallbackQuery,
    user: User,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository,
    student_group_repo: StudentGroupRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    present_ids = set(attendee_ids(lesson.attendees)) if lesson.attendees else set()
    group_ids = set(
        hide_service_groups(await teacher_group_repo.get_groups_for_teacher(lesson.teacher_id))
    )
    all_students = await student_repo.get_all()
    candidates_by_id: dict[str, Student] = {}
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

    group = await group_repo.get_by_id(lesson.group_id) if lesson.group_id else None
    per_visit = bool(group and group.billing_mode == GroupBillingMode.PER_VISIT)
    rows = []
    for student in candidates:
        row = [InlineKeyboardButton(
            text=student.name,
            callback_data=f"lesson_guest_pick:{lesson_id}:{student.student_id}",
        )]
        if per_visit:  # пробное занятие: отметить, но не начислять
            row.append(InlineKeyboardButton(
                text="🆓 проб.",
                callback_data=f"lesson_guest_trial:{lesson_id}:{student.student_id}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="« Назад",
        callback_data=f"lesson_detail:{lesson_id}",
    )])
    await callback.message.edit_text(
        f"<b>Выберите ученика</b> (уже отмечено: {len(present_ids)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_guest_trial:"), TeacherOrAdmin())
async def cb_lesson_guest_trial(
    callback: CallbackQuery,
    user: User,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    lesson_service: LessonService,
    state: FSMContext,
) -> None:
    """Гость на пробном: отмечаем в занятии, но не начисляем (0 ₽)."""
    await _add_guest(callback, user, lesson_repo, group_repo, student_repo,
                     submission_repo, lesson_service, trial=True)


@router.callback_query(F.data.startswith("lesson_guest_pick:"), TeacherOrAdmin())
async def cb_lesson_guest_pick(
    callback: CallbackQuery,
    user: User,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    lesson_service: LessonService,
    state: FSMContext,
) -> None:
    await _add_guest(callback, user, lesson_repo, group_repo, student_repo,
                     submission_repo, lesson_service, trial=False)


async def _add_guest(
    callback: CallbackQuery,
    user: User,
    lesson_repo: LessonRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    lesson_service: LessonService,
    trial: bool,
) -> None:
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
    new_attendees = await lesson_service.add_guest(lesson, student_id, group, trial=trial)
    if new_attendees is None:
        await callback.answer("Этот ученик уже отмечен.", show_alert=True)
        return

    await callback.answer(f"✅ {student.name} добавлен(а){' — пробное, 0 ₽' if trial else ''}.")
    lesson.attendees = new_attendees
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="« К занятию",
                callback_data=f"lesson_detail:{lesson_id}",
            ),
        ]]),
    )
