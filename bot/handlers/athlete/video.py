"""Видео выполнения домашнего задания от спортсмена (решение владельца 02.11.2026).

Вход — «📹 Отправить видео» у задания в кабинете: API пишет в чат «пришлите видео для задания №3» и ставит
состояние с номером задания, кабинет открывает чат. Ролик, присланный без состояния: одно открытое задание —
прикрепляется к нему, несколько — бот спрашивает, к какому (`hwv:pick:`). Файл остаётся в Telegram
(`bot/services/homework_video.py`), педагогу уходит сразу, спортсмену — подтверждение. Не спортсмен — молчим.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.keyboards.athlete import kb_athlete_menu
from bot.repositories import TeacherRepository
from bot.services import DiaryService
from bot.services.homework_video import open_task_markup, send_ref, task_caption, video_ref
from bot.states import HomeworkVideoStates
from bot.utils.notify import notify_safely
from ._base import router

logger = logging.getLogger(__name__)

_IS_VIDEO = F.video | F.video_note | F.document.mime_type.startswith("video/")
_kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="hwv:cancel")]])
_kb_done = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✔️ Готово", callback_data="hwv:cancel")]])


async def _title(diary_service: DiaryService, task) -> str:
    from bot.api.homework import homework_title
    return homework_title((await diary_service.homework_numbers()).get(task.batch_id, 0), task.created_at)


def _plain_label(title: str, task) -> str:
    from bot.api.homework import short
    return f"{title} «{short(task.exercise)}»"


def _label(title: str, task) -> str:
    return escape(_plain_label(title, task))


async def _attach(reply, student, task, ref: str, diary_service: DiaryService,
                  teacher_repo: TeacherRepository, bot, more: bool) -> None:
    """Прикрепить ролик к заданию, отправить педагогу, подтвердить спортсмену."""
    updated = await diary_service.add_task_video(task.task_id, student.student_id, ref)
    title = await _title(diary_service, task)
    if updated is None:
        await reply(f"Задание {_label(title, task)} уже закрыто — видео не прикреплено.", reply_markup=kb_athlete_menu())
        return
    teacher = await teacher_repo.get_by_id(task.teacher_id)
    if teacher is not None and teacher.tg_id:
        total = len(updated.videos)
        await notify_safely(
            send_ref(bot, teacher.tg_id, ref, task_caption(student.name, title, task.exercise, total, total),
                     open_task_markup(task.batch_id)),
            "Видео к заданию педагогу не отправлено: %s")
    logger.info("Видео к заданию %s от %s (%d-е)", task.task_id, student.student_id, len(updated.videos))
    who = f" педагогу {escape(teacher.name)}" if teacher is not None else ""
    text = f"✅ Видео отправлено{who}: {_label(title, task)}."
    if more:
        await reply(text + "\n\nМожно прислать ещё один ролик к этому же заданию.", reply_markup=_kb_done)
    else:
        await reply(text, reply_markup=kb_athlete_menu())


@router.message(StateFilter(None, HomeworkVideoStates.waiting_video), _IS_VIDEO)
async def on_homework_video(
    message: Message, state: FSMContext, diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    student = await diary_service.athlete_by_tg(message.from_user.id)
    ref = video_ref(message)
    if student is None or ref is None:
        return                                        # не спортсмен — видео в чат бота ничего не значит
    data = await state.get_data()
    task = None
    waiting = await state.get_state() == HomeworkVideoStates.waiting_video.state
    if waiting and data.get("hwv_task"):              # кабинет уже сказал, к какому заданию
        task = await diary_service.task(data["hwv_task"])
        if task is None or task.student_id != student.student_id or task.status != "open":
            task, waiting = None, False
            await state.clear()
    if task is None:
        open_tasks = await diary_service.open_tasks(student.student_id)
        if not open_tasks:
            await message.answer("📋 Открытых заданий нет — видео не к чему прикрепить.", reply_markup=kb_athlete_menu())
            return
        if len(open_tasks) > 1:                       # спросим, к какому
            await state.set_state(HomeworkVideoStates.waiting_video)
            await state.update_data(hwv_file=ref, hwv_task=None)
            rows = [[InlineKeyboardButton(text=_plain_label(await _title(diary_service, t), t)[:60],
                                          callback_data=f"hwv:pick:{t.task_id}")] for t in open_tasks]
            rows.append(_kb_cancel.inline_keyboard[0])
            await message.answer("📹 К какому заданию это видео?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        task = open_tasks[0]
    await _attach(message.answer, student, task, ref, diary_service, teacher_repo, message.bot, more=waiting)


@router.callback_query(F.data.startswith("hwv:pick:"))
async def cb_homework_video_pick(
    callback: CallbackQuery, state: FSMContext, diary_service: DiaryService, teacher_repo: TeacherRepository,
) -> None:
    student = await diary_service.athlete_by_tg(callback.from_user.id)
    data = await state.get_data()
    ref = data.get("hwv_file")
    if student is None or not ref:
        await callback.answer("Пришлите видео ещё раз", show_alert=True)
        await state.clear()
        return
    task = await diary_service.task(callback.data.split(":", 2)[2])
    if task is None or task.student_id != student.student_id:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await state.clear()
    await _attach(callback.message.edit_text, student, task, ref, diary_service, teacher_repo, callback.bot, more=False)
    await callback.answer()


@router.callback_query(F.data == "hwv:cancel")
async def cb_homework_video_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    waiting = await state.get_state() == HomeworkVideoStates.waiting_video.state
    await state.clear()
    await callback.message.edit_text("Готово." if waiting else "Хорошо.", reply_markup=kb_athlete_menu())
    await callback.answer()
