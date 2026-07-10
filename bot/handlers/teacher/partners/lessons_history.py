from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, LessonRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.handlers.access import is_teacher as _is_teacher

from ._base import router

logger = logging.getLogger(__name__)


# ─── Занятия ученика за выбранный период ─────────────────────────────────────

def _stu_ids(lesson) -> set[str]:
    """Все student_id, задействованные в занятии."""
    ids = set()
    for sid in (lesson.student_1_id, lesson.student_2_id,
                lesson.student_3_id, lesson.student_4_id):
        if sid:
            ids.add(sid)
    if lesson.attendees:
        for part in lesson.attendees.replace("|", ",").split(","):
            sid = part.strip().split(":")[0].strip()
            if sid and sid.startswith("STU-"):
                ids.add(sid)
    return ids


@router.callback_query(F.data.startswith("t_stu_les:"))
async def cb_t_stu_lessons_months(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    months = sorted(
        {ls.date[:7] for ls in lessons if student_id in _stu_ids(ls)},
        reverse=True,
    )
    if not months:
        await callback.answer(f"Занятий с {student.name} не найдено.", show_alert=True)
        return

    from bot.utils.dates import display_period
    rows = [
        [InlineKeyboardButton(
            text=display_period(ym),
            callback_data=f"t_stu_les_m:{student_id}:{ym}",
        )]
        for ym in months
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_student_card:{student_id}")])
    await callback.message.edit_text(
        f"<b>📋 Занятия с {student.name}</b>\nВыберите период:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_stu_les_m:"))
async def cb_t_stu_lessons_list(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, ym = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    stu_lessons = sorted(
        [ls for ls in lessons if ls.date[:7] == ym and student_id in _stu_ids(ls)],
        key=lambda ls: ls.date,
    )
    back_cb = f"t_stu_les:{student_id}"

    if not stu_lessons:
        from bot.utils.dates import display_period
        await callback.message.edit_text(
            f"Занятий с {student_name} за {display_period(ym)} не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
            ]),
        )
        await callback.answer()
        return

    locked_period = bool(
        await submission_repo.get_by_teacher_and_period(user.teacher_id, ym)
    )
    total_min = sum(ls.duration_min for ls in stu_lessons)

    await state.update_data(t_stu_les_back=f"t_stu_les_m:{student_id}:{ym}")

    from bot.utils.dates import display_period, format_date_short_with_wd
    from bot.models.enums import LessonType
    rows = []
    for ls in stu_lessons:
        lock = "🔒 " if locked_period else ""
        date_s = format_date_short_with_wd(ls.date)
        if ls.type == LessonType.GROUP:
            who = "группа"
        else:
            names = [n for n in (ls.student_1_name, ls.student_2_name,
                                 ls.student_3_name, ls.student_4_name) if n]
            who = " + ".join(names) if names else student_name
        rows.append([InlineKeyboardButton(
            text=f"{lock}{date_s} · {ls.duration_min}м · {who}",
            callback_data=f"lesson_detail:{ls.lesson_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    lock_note = " · 🔒 период сдан" if locked_period else ""
    await callback.message.edit_text(
        f"<b>{student_name} · {display_period(ym)}{lock_note}</b>\n"
        f"Занятий: {len(stu_lessons)} · {total_min} мин",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_pair_les:"))
async def cb_t_pair_lessons_months(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, partner_id = callback.data.split(":", 2)
    student = await student_repo.get_by_id(student_id)
    partner = await student_repo.get_by_id(partner_id)
    pair_label = f"{student.name} + {partner.name}" if student and partner else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    months = sorted(
        {
            ls.date[:7] for ls in lessons
            if student_id in _stu_ids(ls) and partner_id in _stu_ids(ls)
        },
        reverse=True,
    )
    if not months:
        await callback.answer("Парных занятий не найдено.", show_alert=True)
        return

    from bot.utils.dates import display_period
    rows = [
        [InlineKeyboardButton(
            text=display_period(ym),
            callback_data=f"t_pair_les_m:{student_id}:{partner_id}:{ym}",
        )]
        for ym in months
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"t_pair_card:{student_id}")])
    await callback.message.edit_text(
        f"<b>📋 Занятия пары {pair_label}</b>\nВыберите период:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_pair_les_m:"))
async def cb_t_pair_lessons_list(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository, student_repo: StudentRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, partner_id, ym = callback.data.split(":", 3)
    student = await student_repo.get_by_id(student_id)
    partner = await student_repo.get_by_id(partner_id)
    pair_label = f"{student.name} + {partner.name}" if student and partner else student_id

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    pair_lessons = sorted(
        [
            ls for ls in lessons
            if ls.date[:7] == ym
            and student_id in _stu_ids(ls)
            and partner_id in _stu_ids(ls)
        ],
        key=lambda ls: ls.date,
    )
    back_cb = f"t_pair_les:{student_id}:{partner_id}"

    if not pair_lessons:
        from bot.utils.dates import display_period
        await callback.message.edit_text(
            f"Парных занятий за {display_period(ym)} не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
            ]),
        )
        await callback.answer()
        return

    locked_period = bool(
        await submission_repo.get_by_teacher_and_period(user.teacher_id, ym)
    )
    total_min = sum(ls.duration_min for ls in pair_lessons)
    await state.update_data(t_stu_les_back=f"t_pair_les_m:{student_id}:{partner_id}:{ym}")

    from bot.utils.dates import display_period, format_date_short_with_wd
    rows = []
    for ls in pair_lessons:
        lock = "🔒 " if locked_period else ""
        date_s = format_date_short_with_wd(ls.date)
        names = [n for n in (ls.student_1_name, ls.student_2_name,
                             ls.student_3_name, ls.student_4_name) if n]
        who = " + ".join(names) if names else pair_label
        rows.append([InlineKeyboardButton(
            text=f"{lock}{date_s} · {ls.duration_min}м · {who}",
            callback_data=f"lesson_detail:{ls.lesson_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])

    lock_note = " · 🔒 период сдан" if locked_period else ""
    await callback.message.edit_text(
        f"<b>{pair_label} · {display_period(ym)}{lock_note}</b>\n"
        f"Занятий: {len(pair_lessons)} · {total_min} мин",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()

