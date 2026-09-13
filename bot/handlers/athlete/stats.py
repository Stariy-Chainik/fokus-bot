"""Статистика спортсмена и рейтинг."""
from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.services import DiaryService
from bot.keyboards.athlete import kb_stats, kb_rating
from bot.utils.diary_topics import ALL_TOPICS
from bot.utils.diary_format import stats_text, leaderboard_text
from ._base import router, athlete_of, periods


@router.callback_query(F.data == "ath:stats")
@router.callback_query(F.data.startswith("ath:stats:"))
async def cb_stats(callback: CallbackQuery, diary_service: DiaryService) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    this, prev = periods()
    parts = callback.data.split(":")
    period = parts[2] if len(parts) > 2 else this
    st = await diary_service.stats(student.student_id, period)
    board = await diary_service.leaderboard(period)
    mine = next((r for r in board if r.student_id == student.student_id), None)
    text = "📊 <b>Моя статистика</b>\n\n" + stats_text(
        st, period, place=mine.place if mine and st.sessions else None, total=len(board),
    )
    await callback.message.edit_text(text, reply_markup=kb_stats(period, this, prev))
    await callback.answer()


def parse_rating_cb(data: str, default_period: str) -> tuple[str, str]:
    """'prefix:rating[:period[:topic]]' → (period, topic_key)."""
    parts = data.split(":")
    period = parts[2] if len(parts) > 2 else default_period
    topic = parts[3] if len(parts) > 3 else "all"
    return period, topic


def topic_by_key(key: str):
    if key == "all" or not key.isdigit() or int(key) >= len(ALL_TOPICS):
        return None
    return ALL_TOPICS[int(key)]


@router.callback_query(F.data == "ath:rating")
@router.callback_query(F.data.startswith("ath:rating:"))
async def cb_rating(callback: CallbackQuery, diary_service: DiaryService) -> None:
    student = await athlete_of(callback, diary_service)
    if student is None:
        return
    this, prev = periods()
    period, key = parse_rating_cb(callback.data or "", this)
    topic = topic_by_key(key)
    rows = await diary_service.leaderboard(period, topic)
    my_topics = await diary_service.topics_for(student)
    filters = [(ALL_TOPICS.index(t), t) for t in my_topics if t in ALL_TOPICS and t != "Другое"]
    text = leaderboard_text(rows, period, topic, highlight=student.student_id, limit=10)
    await callback.message.edit_text(
        text, reply_markup=kb_rating("ath:rating", period, this, prev, filters, key, back_cb="ath:menu"),
    )
    await callback.answer()
