"""Оценка тренировки: 1–5 → комментарий → уведомления спортсмену и родителям."""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.states import GradeEntryStates
from bot.utils.dates import format_date_display
from bot.utils.diary_format import entry_full, stars
from bot.utils.notify import notify
from ._base import router, actor, grader_id, visible_athlete
from .listing import render_entry

logger = logging.getLogger(__name__)


def kb_grade(entry_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(g), callback_data=f"tgrade:v:{g}") for g in range(1, 6)],
        [InlineKeyboardButton(text="« Отмена", callback_data=f"tdiary:entry:{entry_id}")],
    ])


def kb_grade_comment(entry_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без комментария", callback_data="tgrade:skip")],
        [InlineKeyboardButton(text="« Отмена", callback_data=f"tdiary:entry:{entry_id}")],
    ])


@router.callback_query(F.data.startswith("tgrade:start:"))
async def cb_grade_start(
    callback: CallbackQuery, user: User | None, state: FSMContext, diary_service: DiaryService,
) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    entry_id = callback.data.split(":", 2)[2]
    entry = await diary_service.entry(entry_id)
    student = await visible_athlete(entry.student_id, user, diary_service) if entry else None
    if entry is None or student is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.set_state(GradeEntryStates.choosing_grade)
    await state.update_data(td_entry=entry_id)
    tasks = await diary_service.tasks_map(student.student_id)
    await callback.message.edit_text(
        f"📓 <b>{student.name}</b>\n\n{entry_full(entry, tasks)}\n\n"
        "Оцените качество работы от 1 до 5:",
        reply_markup=kb_grade(entry_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tgrade:v:"), GradeEntryStates.choosing_grade)
async def cb_grade_value(callback: CallbackQuery, state: FSMContext) -> None:
    grade = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    await state.set_state(GradeEntryStates.entering_comment)
    await state.update_data(td_grade=grade)
    await callback.message.edit_text(
        f"Оценка: <b>{grade}/5</b> {stars(grade)}\n\n"
        "Напишите комментарий для спортсмена (что хорошо, что исправить) — или пропустите.",
        reply_markup=kb_grade_comment(data.get("td_entry", "")),
    )
    await callback.answer()


async def _finish(
    target, user: User, state: FSMContext, diary_service: DiaryService,
    teacher_repo: TeacherRepository, comment: str,
) -> None:
    data = await state.get_data()
    entry_id, grade = data.get("td_entry"), data.get("td_grade")
    from bot.handlers.common import _clear_state_preserve_role
    await _clear_state_preserve_role(state)
    if not entry_id or not grade:
        await (target.message if isinstance(target, CallbackQuery) else target).answer("Начните заново.")
        return
    entry = await diary_service.grade_entry(entry_id, int(grade), comment, grader_id(user, target.from_user.id))
    student = await visible_athlete(entry.student_id, user, diary_service) if entry else None
    if entry is None or student is None:
        await (target.message if isinstance(target, CallbackQuery) else target).answer("Запись не найдена.")
        return
    teacher = await teacher_repo.get_by_id(user.teacher_id) if user.teacher_id else None
    who = teacher.name if teacher else "Администратор"
    topics = ", ".join(entry.topics) if entry.topics else "—"
    note = f"\n📝 {comment}" if comment else ""
    await notify(target.bot, [student.athlete_tg_id],
                 f"⭐ <b>{who}</b> оценил(а) вашу тренировку {format_date_display(entry.date)} "
                 f"({entry.minutes} мин, {topics}): <b>{grade}/5</b> {stars(int(grade))}{note}")
    await notify(target.bot, student.parent_tg_ids,
                 f"⭐ <b>{student.name}</b>: тренировка {format_date_display(entry.date)} "
                 f"({entry.minutes} мин, {topics}) оценена педагогом {who}: <b>{grade}/5</b>{note}")
    logger.info("Оценка %s ← %s: %s", entry_id, grader_id(user, target.from_user.id), grade)
    if isinstance(target, CallbackQuery):
        await render_entry(target, entry, student, diary_service, teacher_repo)
    else:
        tasks = await diary_service.tasks_map(student.student_id)
        from .listing import kb_entry
        await target.answer(
            f"✅ Оценка сохранена\n\n📓 <b>{student.name}</b>\n\n" + entry_full(entry, tasks, who),
            reply_markup=kb_entry(entry, entry.date[:7]),
        )


@router.callback_query(F.data == "tgrade:skip", GradeEntryStates.entering_comment)
async def cb_grade_skip(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    await _finish(callback, user, state, diary_service, teacher_repo, "")
    await callback.answer("Оценка сохранена")


@router.message(GradeEntryStates.entering_comment, F.text)
async def on_grade_comment(
    message: Message, user: User | None, state: FSMContext,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    await _finish(message, user, state, diary_service, teacher_repo, (message.text or "").strip()[:500])
