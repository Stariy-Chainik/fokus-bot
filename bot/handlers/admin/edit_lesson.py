from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.models import User
from bot.repositories import LessonRepository, TeacherRepository
from bot.services import LessonService
from bot.keyboards.admin import kb_teacher_list, kb_back
from bot.keyboards.teacher import kb_lesson_list

logger = logging.getLogger(__name__)
router = Router(name="admin_edit_lesson")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


@router.callback_query(F.data == "admin:edit_lesson")
async def cb_edit_lesson_choose_teacher(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:menu"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "<b>Выберите педагога:</b>", reply_markup=kb_teacher_list(teachers, "admin_lessons_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_lessons_teacher:"))
async def cb_admin_lessons_list(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    lessons = await lesson_repo.get_by_teacher(teacher_id)
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    if not lessons:
        await callback.message.edit_text("У педагога нет занятий.", reply_markup=kb_back("admin:edit_lesson"))
        await callback.answer()
        return
    await callback.message.edit_text(
        f"<b>Занятия педагога {teacher_id} ({len(lessons)}):</b>",
        reply_markup=kb_lesson_list(lessons, page=0, page_size=5),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_lesson:"))
async def cb_admin_delete_lesson(
    callback: CallbackQuery, user: User | None, lesson_service: LessonService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    ok = await lesson_service.delete(lesson_id)
    text = f"Занятие {lesson_id} удалено." if ok else "Занятие не найдено."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:edit_lesson"))
    await callback.answer()
