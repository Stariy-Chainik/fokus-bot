"""Дневники спортсменов для педагога и администратора: роутер, гард, рендер дневника."""
from __future__ import annotations
import logging
from typing import Optional

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, Student
from bot.services import DiaryService
from bot.handlers.access import is_teacher_or_admin, is_admin
from bot.keyboards.athlete import kb_period_toggle
from bot.utils.dates import last_periods, display_period, format_date_display
from bot.utils.diary_format import minutes_human

logger = logging.getLogger(__name__)
router = Router(name="teacher_diary")


def actor(user: User | None) -> tuple[bool, Optional[str], bool]:
    """(доступ есть, teacher_id, is_admin)."""
    if not is_teacher_or_admin(user):
        return False, None, False
    return True, user.teacher_id, is_admin(user)


def grader_id(user: User, tg_id: int) -> str:
    return user.teacher_id or f"ADM:{tg_id}"


def periods() -> tuple[str, str]:
    this, prev = last_periods(2)
    return this, prev


async def visible_athlete(
    student_id: str, user: User, diary_service: DiaryService,
) -> Optional[Student]:
    """Спортсмен виден педагогу (общие группы) или админу; иначе None."""
    _, teacher_id, admin = actor(user)
    for s in await diary_service.athletes_for_teacher(teacher_id, admin):
        if s.student_id == student_id:
            return s
    return None


def kb_student_diary(student: Student, entries: list, period: str, open_tasks: int) -> InlineKeyboardMarkup:
    this, prev = periods()
    rows = []
    for e in entries[:20]:
        grade = f"⭐{e.grade}" if e.grade else "🆕"
        topics = ", ".join(e.topics[:2]) + ("…" if len(e.topics) > 2 else "")
        rows.append([InlineKeyboardButton(
            text=f"{grade} {format_date_display(e.date)[:5]} · {e.minutes} мин · {topics}",
            callback_data=f"tdiary:entry:{e.entry_id}",
        )])
    rows.append(kb_period_toggle(f"tdiary:stu:{student.student_id}", period, this, prev))
    rows.append([
        InlineKeyboardButton(text="➕ Задание", callback_data=f"ttask:new:{student.student_id}"),
        InlineKeyboardButton(text=f"📋 Задания ({open_tasks})", callback_data=f"ttask:list:{student.student_id}"),
    ])
    rows.append([InlineKeyboardButton(text="« К спортсменам", callback_data="tdiary:list")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_student_diary(
    callback: CallbackQuery, student: Student, period: str, diary_service: DiaryService,
) -> None:
    entries = await diary_service.entries_for_student(student.student_id, period=period)
    st = await diary_service.stats(student.student_id, period)
    open_tasks = await diary_service.open_tasks(student.student_id)
    board = await diary_service.leaderboard(period)
    mine = next((r for r in board if r.student_id == student.student_id), None)
    unrated = sum(1 for e in entries if not e.grade)
    lines = [f"📓 <b>{student.name}</b> · {display_period(period)}"]
    if entries:
        lines.append(f"Тренировок: {st.sessions} · в зале: {minutes_human(st.total_minutes)}"
                     + (f" · средняя оценка {st.avg_grade}" if st.avg_grade is not None else ""))
        if mine and st.sessions:
            lines.append(f"Рейтинг: {mine.place} место из {len(board)} · {mine.points} оч.")
        if unrated:
            lines.append(f"🆕 Без оценки: {unrated}")
        if st.by_topic:
            lines.append("По танцам: " + ", ".join(f"{t} {minutes_human(m)}" for t, m in list(st.by_topic.items())[:5]))
    else:
        lines.append("Записей за этот месяц нет.")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb_student_diary(student, entries, period, len(open_tasks)),
    )
