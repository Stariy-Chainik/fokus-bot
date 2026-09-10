"""Мои тренировки: список за месяц, карточка записи, удаление (пока нет оценки)."""
from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.keyboards.athlete import kb_entries, kb_entry_detail, kb_entry_delete_confirm
from bot.utils.dates import display_period
from bot.utils.diary_format import entry_full, minutes_human
from ._base import router, athlete_of, periods


async def _show_list(callback: CallbackQuery, diary_service: DiaryService, period: str) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    this, prev = periods()
    entries = await diary_service.entries_for_student(student.student_id, period=period)
    total = sum(e.minutes for e in entries)
    text = f"📓 <b>Мои тренировки · {display_period(period)}</b>\n"
    text += f"{len(entries)} тренировок · {minutes_human(total)}" if entries else "Записей за этот месяц нет."
    await callback.message.edit_text(text, reply_markup=kb_entries(entries, period, this, prev))
    await callback.answer()


@router.callback_query(F.data == "ath:entries")
async def cb_entries(callback: CallbackQuery, diary_service: DiaryService) -> None:
    await _show_list(callback, diary_service, periods()[0])


@router.callback_query(F.data.startswith("athent:list:"))
async def cb_entries_period(callback: CallbackQuery, diary_service: DiaryService) -> None:
    await _show_list(callback, diary_service, callback.data.split(":", 2)[2])


@router.callback_query(F.data.startswith("athent:view:"))
async def cb_entry_view(
    callback: CallbackQuery, diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    entry = await diary_service.entry(callback.data.split(":", 2)[2])
    if entry is None or entry.student_id != student.student_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    tasks = await diary_service.tasks_map(student.student_id)
    grader = ""
    if entry.graded_by:
        t = await teacher_repo.get_by_id(entry.graded_by)
        grader = t.name if t else "администратор"
    await callback.message.edit_text(
        "📓 <b>Тренировка</b>\n\n" + entry_full(entry, tasks, grader),
        reply_markup=kb_entry_detail(entry, can_delete=not entry.grade),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("athent:del:"))
async def cb_entry_delete(callback: CallbackQuery, diary_service: DiaryService) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    entry = await diary_service.entry(callback.data.split(":", 2)[2])
    if entry is None or entry.student_id != student.student_id or entry.grade:
        await callback.answer("Удалить нельзя", show_alert=True)
        return
    await callback.message.edit_text(
        "Удалить запись?\n\n" + entry_full(entry), reply_markup=kb_entry_delete_confirm(entry),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("athent:del_ok:"))
async def cb_entry_delete_ok(callback: CallbackQuery, diary_service: DiaryService) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    entry_id = callback.data.split(":", 2)[2]
    entry = await diary_service.entry(entry_id)
    period = entry.date[:7] if entry else periods()[0]
    ok = await diary_service.delete_entry(entry_id, student.student_id)
    await callback.answer("Запись удалена" if ok else "Удалить нельзя", show_alert=not ok)
    await _show_list(callback, diary_service, period)
