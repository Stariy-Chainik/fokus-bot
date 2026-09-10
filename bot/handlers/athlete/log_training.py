"""Запись тренировки: дата → минуты → темы → задания → комментарий → сохранение."""
from __future__ import annotations
import logging
from datetime import date

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.services import DiaryService
from bot.states import LogTrainingStates
from bot.keyboards.calendar import kb_calendar
from bot.keyboards.athlete import (
    kb_log_date, kb_log_minutes, kb_log_topics, kb_log_tasks, kb_log_comment, kb_log_done,
)
from bot.utils.dates import format_date_display
from bot.utils.diary_format import entry_full
from ._base import router, athlete_of

logger = logging.getLogger(__name__)

MIN_MINUTES, MAX_MINUTES = 5, 600


def _header(data: dict) -> str:
    parts = []
    if data.get("ath_date"):
        parts.append(f"📅 {format_date_display(data['ath_date'])}")
    if data.get("ath_minutes"):
        parts.append(f"⏱ {data['ath_minutes']} мин")
    sel = data.get("ath_sel") or []
    topics = data.get("ath_topics") or []
    if sel:
        parts.append("🎵 " + ", ".join(topics[i] for i in sel if i < len(topics)))
    return " · ".join(parts) + ("\n\n" if parts else "")


# ─── Дата ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ath:log")
async def cb_log_start(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    await state.set_state(LogTrainingStates.choosing_date)
    await state.update_data(ath_date=None, ath_minutes=None, ath_sel=[], ath_tsel=[])
    await callback.message.edit_text("➕ <b>Новая тренировка</b>\n\nКогда тренировались?", reply_markup=kb_log_date())
    await callback.answer()


@router.callback_query(F.data == "athlog:back:date", LogTrainingStates.choosing_minutes)
@router.callback_query(F.data == "athlog:back:date", LogTrainingStates.entering_minutes)
async def cb_log_back_date(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LogTrainingStates.choosing_date)
    await callback.message.edit_text("➕ <b>Новая тренировка</b>\n\nКогда тренировались?", reply_markup=kb_log_date())
    await callback.answer()


async def _ask_minutes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LogTrainingStates.choosing_minutes)
    data = await state.get_data()
    await callback.message.edit_text(_header(data) + "Сколько времени провели в зале?", reply_markup=kb_log_minutes())


@router.callback_query(F.data.startswith("athlog:date:"), LogTrainingStates.choosing_date)
async def cb_log_date(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 2)[2]
    if value == "manual":
        today = date.today()
        await callback.message.edit_text(
            "Выберите дату тренировки:",
            reply_markup=kb_calendar(today.year, today.month, prefix="athcal", max_date=today, cancel_cb="ath:menu"),
        )
        await callback.answer()
        return
    await state.update_data(ath_date=value)
    await _ask_minutes(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("athcal_nav:"), LogTrainingStates.choosing_date)
async def cb_log_cal_nav(callback: CallbackQuery) -> None:
    year, month = (int(x) for x in callback.data.split(":", 1)[1].split("-"))
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="athcal", max_date=date.today(), cancel_cb="ath:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("athcal_pick:"), LogTrainingStates.choosing_date)
async def cb_log_cal_pick(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    if date.fromisoformat(value) > date.today():
        await callback.answer("Дата в будущем", show_alert=True)
        return
    await state.update_data(ath_date=value)
    await _ask_minutes(callback, state)
    await callback.answer()


# ─── Минуты ──────────────────────────────────────────────────────────────────

async def _ask_topics(target, state: FSMContext, diary_service: DiaryService, tg_id: int) -> None:
    student = await diary_service.athlete_by_tg(tg_id)
    topics = await diary_service.topics_for(student) if student else []
    data = await state.get_data()
    sel = set(data.get("ath_sel") or [])
    await state.set_state(LogTrainingStates.choosing_topics)
    await state.update_data(ath_topics=topics)
    text = _header(data) + "Над чем работали? Можно выбрать несколько."
    kb = kb_log_topics(topics, sel)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("athlog:min:"), LogTrainingStates.choosing_minutes)
@router.callback_query(F.data.startswith("athlog:min:"), LogTrainingStates.entering_minutes)
async def cb_log_minutes(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    value = callback.data.split(":", 2)[2]
    if value == "manual":
        await state.set_state(LogTrainingStates.entering_minutes)
        await callback.message.edit_text(
            _header(await state.get_data()) + f"Введите число минут ({MIN_MINUTES}–{MAX_MINUTES}):",
            reply_markup=kb_log_minutes(),
        )
        await callback.answer()
        return
    await state.update_data(ath_minutes=int(value))
    await _ask_topics(callback, state, diary_service, callback.from_user.id)
    await callback.answer()


@router.message(LogTrainingStates.entering_minutes, F.text)
async def on_minutes_text(message: Message, state: FSMContext, diary_service: DiaryService) -> None:
    raw = (message.text or "").strip().replace("мин", "").strip()
    if not raw.isdigit() or not MIN_MINUTES <= int(raw) <= MAX_MINUTES:
        await message.answer(f"Введите число минут от {MIN_MINUTES} до {MAX_MINUTES}:")
        return
    await state.update_data(ath_minutes=int(raw))
    await _ask_topics(message, state, diary_service, message.from_user.id)


@router.callback_query(F.data == "athlog:back:min", LogTrainingStates.choosing_topics)
async def cb_log_back_min(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_minutes(callback, state)
    await callback.answer()


# ─── Темы ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("athlog:tp:"), LogTrainingStates.choosing_topics)
async def cb_log_topic_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    topics = data.get("ath_topics") or []
    sel = set(data.get("ath_sel") or [])
    if idx >= len(topics):
        await callback.answer()
        return
    sel.symmetric_difference_update({idx})
    await state.update_data(ath_sel=sorted(sel))
    await callback.message.edit_reply_markup(reply_markup=kb_log_topics(topics, sel))
    await callback.answer()


async def _ask_tasks_or_comment(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    student = await diary_service.athlete_by_tg(callback.from_user.id)
    tasks = await diary_service.open_tasks(student.student_id) if student else []
    data = await state.get_data()
    if not tasks:
        await state.update_data(ath_tasks=[], ath_tsel=[])
        await _ask_comment(callback, state, back_cb="athlog:back:tp")
        return
    pairs = [(t.task_id, f"{t.exercise} · {t.minutes} мин") for t in tasks]
    await state.set_state(LogTrainingStates.choosing_tasks)
    await state.update_data(ath_tasks=pairs)
    sel = set(data.get("ath_tsel") or [])
    await callback.message.edit_text(
        _header(data) + "Какие задания педагога отработали?", reply_markup=kb_log_tasks(pairs, sel),
    )


@router.callback_query(F.data == "athlog:tp_done", LogTrainingStates.choosing_topics)
async def cb_log_topics_done(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    data = await state.get_data()
    if not data.get("ath_sel"):
        await callback.answer("Выберите хотя бы одну тему", show_alert=True)
        return
    await _ask_tasks_or_comment(callback, state, diary_service)
    await callback.answer()


@router.callback_query(F.data == "athlog:back:tp", LogTrainingStates.choosing_tasks)
@router.callback_query(F.data == "athlog:back:tp", LogTrainingStates.entering_comment)
async def cb_log_back_topics(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    await _ask_topics(callback, state, diary_service, callback.from_user.id)
    await callback.answer()


# ─── Задания ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("athlog:tk:"), LogTrainingStates.choosing_tasks)
async def cb_log_task_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    tid = callback.data.split(":", 2)[2]
    data = await state.get_data()
    pairs = [tuple(p) for p in data.get("ath_tasks") or []]
    sel = set(data.get("ath_tsel") or [])
    sel.symmetric_difference_update({tid})
    await state.update_data(ath_tsel=sorted(sel))
    await callback.message.edit_reply_markup(reply_markup=kb_log_tasks(pairs, sel))
    await callback.answer()


async def _ask_comment(callback: CallbackQuery, state: FSMContext, back_cb: str) -> None:
    await state.set_state(LogTrainingStates.entering_comment)
    await state.update_data(ath_back=back_cb)
    data = await state.get_data()
    await callback.message.edit_text(
        _header(data) + "Комментарий: что получалось, над чем ещё поработать?\n"
        "Напишите сообщением или пропустите.",
        reply_markup=kb_log_comment(back_cb),
    )


@router.callback_query(F.data == "athlog:tk_done", LogTrainingStates.choosing_tasks)
async def cb_log_tasks_done(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_comment(callback, state, back_cb="athlog:back:tk")
    await callback.answer()


@router.callback_query(F.data == "athlog:back:tk", LogTrainingStates.entering_comment)
async def cb_log_back_tasks(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    await _ask_tasks_or_comment(callback, state, diary_service)
    await callback.answer()


# ─── Комментарий и сохранение ───────────────────────────────────────────────

async def _save(target, state: FSMContext, diary_service: DiaryService, comment: str) -> None:
    tg_id = target.from_user.id
    student = await diary_service.athlete_by_tg(tg_id)
    data = await state.get_data()
    await state.clear()
    await state.update_data(current_role="athlete")
    if student is None or not data.get("ath_date") or not data.get("ath_minutes"):
        text, kb = "Не удалось сохранить: начните заново.", kb_log_done()
    else:
        topics_all = data.get("ath_topics") or []
        topics = [topics_all[i] for i in data.get("ath_sel") or [] if i < len(topics_all)]
        entry = await diary_service.create_entry(
            student.student_id, data["ath_date"], int(data["ath_minutes"]),
            topics, list(data.get("ath_tsel") or []), comment,
        )
        tasks = {t.task_id: t for t in await diary_service.open_tasks(student.student_id)}
        text = "✅ <b>Тренировка записана</b>\n\n" + entry_full(entry, tasks)
        kb = kb_log_done()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "athlog:skip_comment", LogTrainingStates.entering_comment)
async def cb_log_skip_comment(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    await _save(callback, state, diary_service, "")
    await callback.answer()


@router.message(LogTrainingStates.entering_comment, F.text)
async def on_comment_text(message: Message, state: FSMContext, diary_service: DiaryService) -> None:
    comment = (message.text or "").strip()[:500]
    await _save(message, state, diary_service, comment)
