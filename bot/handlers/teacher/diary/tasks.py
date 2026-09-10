"""Задания спортсмену: новое (упражнение → минуты → комментарий), список, закрытие."""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.states import AssignTaskStates
from bot.utils.diary_format import tasks_text
from bot.utils.notify import notify
from ._base import router, actor, periods, visible_athlete, render_student_diary

logger = logging.getLogger(__name__)
MIN_MINUTES, MAX_MINUTES = 1, 300


def _kb_cancel(student_id: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="« Отмена", callback_data=f"tdiary:stu:{student_id}")]


def kb_exercise(recent: list[str], student_id: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"ttask:ex:{i}")] for i, name in enumerate(recent)]
    rows.append([InlineKeyboardButton(text="✍️ Другое упражнение", callback_data="ttask:ex_other")])
    rows.append(_kb_cancel(student_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_minutes(student_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m} мин", callback_data=f"ttask:min:{m}") for m in (5, 10, 15)],
        [InlineKeyboardButton(text=f"{m} мин", callback_data=f"ttask:min:{m}") for m in (20, 30, 45)],
        [InlineKeyboardButton(text="✍️ Другое число", callback_data="ttask:min:manual")],
        _kb_cancel(student_id),
    ])


def kb_comment(student_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без комментария", callback_data="ttask:skip")],
        _kb_cancel(student_id),
    ])


# ─── Новое задание ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ttask:new:"))
async def cb_task_new(
    callback: CallbackQuery, user: User | None, state: FSMContext, diary_service: DiaryService,
) -> None:
    ok, teacher_id, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 2)[2]
    student = await visible_athlete(student_id, user, diary_service)
    if student is None:
        await callback.answer("Спортсмен не найден", show_alert=True)
        return
    recent = await diary_service.recent_exercises(teacher_id) if teacher_id else []
    await state.set_state(AssignTaskStates.choosing_exercise)
    await state.update_data(td_sid=student_id, td_recent=recent, td_ex=None, td_min=None)
    await callback.message.edit_text(
        f"➕ <b>Задание для {student.name}</b>\n\n"
        + ("Выберите упражнение или введите новое:" if recent else "Введите упражнение (например: маленький квадрат):"),
        reply_markup=kb_exercise(recent, student_id),
    )
    if not recent:
        await state.set_state(AssignTaskStates.entering_exercise)
    await callback.answer()


async def _ask_minutes(target, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AssignTaskStates.choosing_minutes)
    text = f"Упражнение: <b>{data.get('td_ex')}</b>\n\nСколько минут в день?"
    kb = kb_minutes(data.get("td_sid", ""))
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ttask:ex:"), AssignTaskStates.choosing_exercise)
async def cb_task_exercise(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    recent = data.get("td_recent") or []
    if idx >= len(recent):
        await callback.answer()
        return
    await state.update_data(td_ex=recent[idx])
    await _ask_minutes(callback, state)
    await callback.answer()


@router.callback_query(F.data == "ttask:ex_other", AssignTaskStates.choosing_exercise)
@router.callback_query(F.data == "ttask:ex_other", AssignTaskStates.entering_exercise)
async def cb_task_exercise_other(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AssignTaskStates.entering_exercise)
    data = await state.get_data()
    await callback.message.edit_text(
        "Введите название упражнения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_kb_cancel(data.get("td_sid", ""))]),
    )
    await callback.answer()


@router.message(AssignTaskStates.entering_exercise, F.text)
async def on_exercise_text(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())[:80].replace("|", "/")
    if len(name) < 2:
        await message.answer("Слишком коротко. Введите название упражнения:")
        return
    await state.update_data(td_ex=name)
    await _ask_minutes(message, state)


async def _ask_comment(target, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AssignTaskStates.entering_comment)
    text = (f"Упражнение: <b>{data.get('td_ex')}</b> · {data.get('td_min')} мин\n\n"
            "Комментарий спортсмену (на что обратить внимание) — или пропустите.")
    kb = kb_comment(data.get("td_sid", ""))
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ttask:min:"), AssignTaskStates.choosing_minutes)
async def cb_task_minutes(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 2)[2]
    if value == "manual":
        await state.set_state(AssignTaskStates.entering_minutes)
        data = await state.get_data()
        await callback.message.edit_text(
            f"Введите число минут ({MIN_MINUTES}–{MAX_MINUTES}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_kb_cancel(data.get("td_sid", ""))]),
        )
        await callback.answer()
        return
    await state.update_data(td_min=int(value))
    await _ask_comment(callback, state)
    await callback.answer()


@router.message(AssignTaskStates.entering_minutes, F.text)
async def on_task_minutes_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace("мин", "").strip()
    if not raw.isdigit() or not MIN_MINUTES <= int(raw) <= MAX_MINUTES:
        await message.answer(f"Введите число минут от {MIN_MINUTES} до {MAX_MINUTES}:")
        return
    await state.update_data(td_min=int(raw))
    await _ask_comment(message, state)


async def _save(target, user: User, state: FSMContext, diary_service: DiaryService,
                teacher_repo: TeacherRepository, comment: str) -> None:
    data = await state.get_data()
    from bot.handlers.common import _clear_state_preserve_role
    await _clear_state_preserve_role(state)
    student_id, exercise, minutes = data.get("td_sid"), data.get("td_ex"), data.get("td_min")
    student = await visible_athlete(student_id, user, diary_service) if student_id else None
    msg = target.message if isinstance(target, CallbackQuery) else target
    if student is None or not exercise or not minutes:
        await msg.answer("Не удалось сохранить задание: начните заново.")
        return
    teacher_id = user.teacher_id or f"ADM:{target.from_user.id}"
    task = await diary_service.create_task(student_id, teacher_id, exercise, int(minutes), comment)
    teacher = await teacher_repo.get_by_id(user.teacher_id) if user.teacher_id else None
    who = teacher.name if teacher else "Администратор"
    note = f"\n💬 {comment}" if comment else ""
    await notify(target.bot, [student.athlete_tg_id],
                 f"📋 <b>Новое задание от {who}</b>\n\n<b>{task.exercise}</b> — {task.minutes} мин{note}\n\n"
                 "Отмечайте задание при записи каждой тренировки.")
    logger.info("Задание %s для %s от %s", task.task_id, student_id, teacher_id)
    if isinstance(target, CallbackQuery):
        await target.answer("Задание отправлено спортсмену")
        await render_student_diary(target, student, periods()[0], diary_service)
    else:
        await msg.answer(f"✅ Задание отправлено: <b>{task.exercise}</b> — {task.minutes} мин",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="« К дневнику", callback_data=f"tdiary:stu:{student_id}")],
                         ]))


@router.callback_query(F.data == "ttask:skip", AssignTaskStates.entering_comment)
async def cb_task_skip(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    await _save(callback, user, state, diary_service, teacher_repo, "")


@router.message(AssignTaskStates.entering_comment, F.text)
async def on_task_comment(
    message: Message, user: User | None, state: FSMContext,
    diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    await _save(message, user, state, diary_service, teacher_repo, (message.text or "").strip()[:300])


# ─── Список и закрытие ───────────────────────────────────────────────────────

async def _render_tasks(callback: CallbackQuery, student, diary_service: DiaryService,
                        teacher_repo: TeacherRepository) -> None:
    tasks = await diary_service.open_tasks(student.student_id)
    usage = await diary_service.task_usage(student.student_id)
    names = {t.teacher_id: t.name for t in await teacher_repo.get_all()}
    rows = [
        [InlineKeyboardButton(text=f"✅ Закрыть: {t.exercise}", callback_data=f"ttask:close:{t.task_id}")]
        for t in tasks
    ]
    rows.append([InlineKeyboardButton(text="➕ Задание", callback_data=f"ttask:new:{student.student_id}")])
    rows.append([InlineKeyboardButton(text="« К дневнику", callback_data=f"tdiary:stu:{student.student_id}")])
    await callback.message.edit_text(
        f"📋 <b>Задания · {student.name}</b>\n\n" + tasks_text(tasks, usage, names),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("ttask:list:"))
async def cb_task_list(
    callback: CallbackQuery, user: User | None, diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    student = await visible_athlete(callback.data.split(":", 2)[2], user, diary_service)
    if student is None:
        await callback.answer("Спортсмен не найден", show_alert=True)
        return
    await _render_tasks(callback, student, diary_service, teacher_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("ttask:close:"))
async def cb_task_close(
    callback: CallbackQuery, user: User | None, diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    task = await diary_service.task(callback.data.split(":", 2)[2])
    student = await visible_athlete(task.student_id, user, diary_service) if task else None
    if task is None or student is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await diary_service.close_task(task.task_id)
    await notify(callback.bot, [student.athlete_tg_id],
                 f"✅ Задание <b>{task.exercise}</b> закрыто педагогом. Молодец!")
    await callback.answer("Задание закрыто")
    await _render_tasks(callback, student, diary_service, teacher_repo)
