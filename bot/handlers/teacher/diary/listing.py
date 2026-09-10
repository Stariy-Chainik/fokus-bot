"""Список спортсменов педагога, дневник ученика, карточка записи."""
from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.utils.diary_format import entry_full
from ._base import router, actor, periods, visible_athlete, render_student_diary


@router.callback_query(F.data.in_({"teacher:diary", "admin:diary", "tdiary:list"}))
async def cb_diary_list(
    callback: CallbackQuery, user: User | None, state: FSMContext, diary_service: DiaryService,
) -> None:
    ok, teacher_id, admin = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    from bot.handlers.common import _clear_state_preserve_role
    await _clear_state_preserve_role(state)
    athletes = await diary_service.athletes_for_teacher(teacher_id, admin)
    unrated = await diary_service.unrated_counts([s.student_id for s in athletes])
    this, _ = periods()
    rows = []
    for s in athletes:
        n = unrated.get(s.student_id, 0)
        label = f"{s.name} 🆕 {n}" if n else s.name
        rows.append([InlineKeyboardButton(text=label, callback_data=f"tdiary:stu:{s.student_id}")])
    rows.append([InlineKeyboardButton(text="🏆 Рейтинг", callback_data=f"tdiary:rating:{this}:all")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    text = "📓 <b>Дневники спортсменов</b>\n\n"
    text += ("🆕 — записи без оценки. Выберите спортсмена:" if athletes
             else "Пока никто из ваших учеников не завёл кабинет спортсмена.\n"
                  "Ученик спортивной группы пишет боту /start → «Я спортсмен».")
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("tdiary:stu:"))
async def cb_diary_student(
    callback: CallbackQuery, user: User | None, state: FSMContext, diary_service: DiaryService,
) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    student_id = parts[2]
    period = parts[3] if len(parts) > 3 else periods()[0]
    student = await visible_athlete(student_id, user, diary_service)
    if student is None:
        await callback.answer("Спортсмен не найден или не в вашей группе", show_alert=True)
        return
    from bot.handlers.common import _clear_state_preserve_role
    await _clear_state_preserve_role(state)
    await render_student_diary(callback, student, period, diary_service)
    await callback.answer()


def kb_entry(entry, period: str) -> InlineKeyboardMarkup:
    label = "✏️ Изменить оценку" if entry.grade else "⭐ Оценить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"tgrade:start:{entry.entry_id}")],
        [InlineKeyboardButton(text="« К дневнику", callback_data=f"tdiary:stu:{entry.student_id}:{period}")],
    ])


async def render_entry(callback: CallbackQuery, entry, student, diary_service: DiaryService,
                       teacher_repo: TeacherRepository) -> None:
    tasks = await diary_service.tasks_map(student.student_id)
    grader = ""
    if entry.graded_by:
        t = await teacher_repo.get_by_id(entry.graded_by)
        grader = t.name if t else "администратор"
    await callback.message.edit_text(
        f"📓 <b>{student.name}</b>\n\n" + entry_full(entry, tasks, grader),
        reply_markup=kb_entry(entry, entry.date[:7]),
    )


@router.callback_query(F.data.startswith("tdiary:entry:"))
async def cb_diary_entry(
    callback: CallbackQuery, user: User | None, diary_service: DiaryService,
    teacher_repo: TeacherRepository,
) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    entry = await diary_service.entry(callback.data.split(":", 2)[2])
    student = await visible_athlete(entry.student_id, user, diary_service) if entry else None
    if entry is None or student is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await render_entry(callback, entry, student, diary_service, teacher_repo)
    await callback.answer()
