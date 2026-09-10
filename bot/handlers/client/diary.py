"""Дневник тренировок ребёнка для родителя — только чтение."""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.repositories import StudentRepository, TeacherRepository
from bot.services import DiaryService
from bot.keyboards.athlete import kb_period_toggle
from bot.utils.dates import last_periods, display_period, format_date_display
from bot.utils.diary_format import stats_text, tasks_text, stars, leaderboard_text

router = Router(name="client_diary")


def _kb(student_id: str, period: str, this: str, prev: str, many: bool) -> InlineKeyboardMarkup:
    rows = [kb_period_toggle(f"cldiary:m:{student_id}", period, this, prev)]
    rows.append([InlineKeyboardButton(text="🏆 Рейтинг группы", callback_data=f"cldiary:rating:{student_id}:{period}")])
    if many:
        rows.append([InlineKeyboardButton(text="« К детям", callback_data="client:diary")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(
    callback: CallbackQuery, student, period: str, many: bool,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    this, prev = last_periods(2)
    entries = await diary_service.entries_for_student(student.student_id, period=period)
    st = await diary_service.stats(student.student_id, period)
    board = await diary_service.leaderboard(period)
    mine = next((r for r in board if r.student_id == student.student_id), None)
    names = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
    lines = [f"📓 <b>Дневник тренировок · {student.name}</b>"]
    if not student.athlete_tg_id:
        lines.append("\nРебёнок ещё не завёл кабинет спортсмена: в боте /start → «Я спортсмен».")
    lines.append("")
    lines.append(stats_text(st, period, place=mine.place if mine and st.sessions else None, total=len(board)))
    if entries:
        tasks_by_id = await diary_service.tasks_map(student.student_id)
        lines.append("\n<b>Тренировки:</b>")
        for e in entries[:15]:
            topics = ", ".join(e.topics) if e.topics else "—"
            grade = f" · ⭐{e.grade} {stars(e.grade)}" if e.grade else " · ⏳ без оценки"
            lines.append(f"• {format_date_display(e.date)[:5]} · {e.minutes} мин · {topics}{grade}")
            if e.task_ids:
                done = [f"{tasks_by_id[t].exercise} ({tasks_by_id[t].minutes} мин)" if t in tasks_by_id else t
                        for t in e.task_ids]
                lines.append("   📋 Задания: " + "; ".join(done))
            if e.comment:
                lines.append(f"   💬 {e.comment}")
            if e.grade_comment:
                who = names.get(e.graded_by, "педагог")
                lines.append(f"   📝 {who}: {e.grade_comment}")
    tasks = await diary_service.open_tasks(student.student_id)
    if tasks:
        usage = await diary_service.task_usage(student.student_id)
        lines.append("\n<b>Задания педагога:</b>\n" + tasks_text(tasks, usage, names))
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=_kb(student.student_id, period, this, prev, many),
    )


@router.callback_query(F.data == "client:diary")
async def cb_client_diary(
    callback: CallbackQuery, student_repo: StudentRepository,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет привязанных детей", show_alert=True)
        return
    if len(students) == 1:
        await _render(callback, students[0], last_periods(1)[0], False, diary_service, teacher_repo)
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=s.name, callback_data=f"cldiary:stu:{s.student_id}")] for s in students]
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="go:home")])
    await callback.message.edit_text("📓 Чей дневник показать?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("cldiary:stu:"))
@router.callback_query(F.data.startswith("cldiary:m:"))
async def cb_client_diary_student(
    callback: CallbackQuery, student_repo: StudentRepository,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    parts = callback.data.split(":")
    student_id = parts[2]
    period = parts[3] if len(parts) > 3 else last_periods(1)[0]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    student = next((s for s in students if s.student_id == student_id), None)
    if student is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _render(callback, student, period, len(students) > 1, diary_service, teacher_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("cldiary:rating:"))
async def cb_client_diary_rating(
    callback: CallbackQuery, student_repo: StudentRepository, diary_service: DiaryService,
) -> None:
    """Полный рейтинг месяца для родителя; свой ребёнок выделен."""
    parts = callback.data.split(":")
    student_id = parts[2]
    period = parts[3] if len(parts) > 3 else last_periods(1)[0]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not any(s.student_id == student_id for s in students):
        await callback.answer("Нет доступа", show_alert=True)
        return
    this, prev = last_periods(2)
    rows = await diary_service.leaderboard(period)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        kb_period_toggle(f"cldiary:rating:{student_id}", period, this, prev),
        [InlineKeyboardButton(text="« К дневнику", callback_data=f"cldiary:m:{student_id}:{period}")],
        [InlineKeyboardButton(text="« Меню", callback_data="go:home")],
    ])
    await callback.message.edit_text(leaderboard_text(rows, period, None, highlight=student_id), reply_markup=kb)
    await callback.answer()
