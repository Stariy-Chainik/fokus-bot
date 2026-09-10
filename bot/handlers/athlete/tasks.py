"""Мои задания: открытые задания педагога с отметками «сделано N раз»."""
from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.keyboards.athlete import kb_menu_only
from bot.utils.diary_format import tasks_text
from ._base import router, athlete_of


@router.callback_query(F.data == "ath:tasks")
async def cb_tasks(callback: CallbackQuery, diary_service: DiaryService, teacher_repo: TeacherRepository) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    tasks = await diary_service.open_tasks(student.student_id)
    usage = await diary_service.task_usage(student.student_id)
    names = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
    text = "📋 <b>Мои задания</b>\n\n" + tasks_text(tasks, usage, names)
    if tasks:
        text += "\n\nОтмечайте отработанные задания при записи тренировки."
    await callback.message.edit_text(text, reply_markup=kb_menu_only())
    await callback.answer()
