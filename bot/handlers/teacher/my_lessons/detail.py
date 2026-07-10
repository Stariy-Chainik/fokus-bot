from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers.common import show_card
from bot.keyboards.teacher import kb_lesson_detail
from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import (
    GroupRepository,
    LessonRepository,
    StudentRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.utils import parse_attendees
from bot.utils.dates import format_date_display

from ._base import (
    _can_view_lesson,
    _is_teacher_or_admin,
    _submitted_periods,
    logger,
    router,
)


@router.callback_query(F.data.startswith("lesson_detail:"))
async def cb_lesson_detail(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    group_repo: GroupRepository,
    student_repo: StudentRepository,
    state: FSMContext,
) -> None:
    if not _is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or not _can_view_lesson(user, lesson):
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    periods = await _submitted_periods(lesson.teacher_id, submission_repo)
    locked = lesson.date[:7] in periods

    lines = [
        f"<b>Занятие {lesson.lesson_id}</b>",
        f"Дата: {format_date_display(lesson.date)}",
        f"Тип: {'Групповое' if lesson.type == LessonType.GROUP else 'Индивидуальное'}",
        f"Длительность: {lesson.duration_min} мин",
    ]
    if lesson.group_id:
        group = await group_repo.get_by_id(lesson.group_id)
        if group:
            lines.append(f"Группа: {group.name}")
    if lesson.student_1_name:
        lines.append(f"Ученик 1: {lesson.student_1_name}")
    if lesson.student_2_name:
        lines.append(f"Ученик 2: {lesson.student_2_name}")
    if lesson.student_3_name:
        lines.append(f"Ученик 3: {lesson.student_3_name}")
    if lesson.student_4_name:
        lines.append(f"Ученик 4: {lesson.student_4_name}")

    if lesson.type == LessonType.GROUP and lesson.attendees:
        entries = parse_attendees(
            lesson.attendees,
            default_duration=lesson.duration_min,
        )
        if entries:
            lines.extend(["", f"<b>Присутствовали ({len(entries)}):</b>"])
            total = 0
            for entry in entries:
                student = await student_repo.get_by_id(entry.student_id)
                name = student.name if student else entry.student_id
                if entry.amount > 0:
                    lines.append(
                        f"  • {name} · {entry.duration_min} мин · {entry.amount}₽"
                    )
                    total += entry.amount
                else:
                    lines.append(
                        f"  • {name} · {entry.duration_min} мин · абонемент"
                    )
            if total > 0:
                lines.append(f"<b>Итого: {total} ₽</b>")

    if locked:
        lines.append("")
        if user.is_admin:
            lines.append(
                "🔒 Период сдан педагогом — редактирование доступно только администратору."
            )
        else:
            lines.append("🔒 Период сдан — редактирование недоступно.")

    data = await state.get_data()
    from_teacher_flow = bool(data.get("lm_mode"))
    if from_teacher_flow:
        tag = data.get("lm_filter_tag")
        if tag:
            back_callback = f"lessons_page:0:{tag}"
        else:
            back_callback = (
                "teacher:lesson_view"
                if data.get("lm_mode") == "view"
                else "teacher:lesson_delete"
            )
    elif data.get("t_stu_les_back"):
        back_callback = data["t_stu_les_back"]
    else:
        back_callback = "admin:edit_lesson" if user.is_admin else "teacher:lesson_delete"
        logger.info(
            "lesson_detail back fallback: lesson=%s user=%s admin=%s → %s "
            "(no lm_mode / t_stu_les_back in FSM — likely state loss)",
            lesson.lesson_id,
            callback.from_user.id,
            user.is_admin,
            back_callback,
        )
    can_add_guest = (
        (not locked or bool(user.is_admin))
        and lesson.type == LessonType.GROUP
        and bool(lesson.group_id)
    )
    await show_card(
        callback,
        "\n".join(lines),
        reply_markup=kb_lesson_detail(
            lesson,
            locked,
            back_cb=back_callback,
            can_add_guest=can_add_guest,
            is_admin=bool(user.is_admin),
        ),
    )
