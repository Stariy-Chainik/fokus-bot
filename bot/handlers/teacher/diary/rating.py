"""Рейтинг спортсменов для педагога/админа: полный список, фильтр по танцу."""
from __future__ import annotations

from aiogram import F
from aiogram.types import CallbackQuery

from bot.models import User
from bot.services import DiaryService
from bot.keyboards.athlete import kb_rating
from bot.utils.diary_topics import ALL_TOPICS, BALLROOM, RHYTHMIC, is_rhythmic
from bot.utils.diary_format import leaderboard_text
from ._base import router, actor, periods


@router.callback_query(F.data.startswith("tdiary:rating:"))
async def cb_teacher_rating(callback: CallbackQuery, user: User | None, diary_service: DiaryService) -> None:
    ok, _, _ = actor(user)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
        return
    this, prev = periods()
    parts = callback.data.split(":")
    period = parts[2] if len(parts) > 2 else this
    key = parts[3] if len(parts) > 3 else "all"
    topic = ALL_TOPICS[int(key)] if key.isdigit() and int(key) < len(ALL_TOPICS) else None
    rows = await diary_service.leaderboard(period, topic)
    # Фильтр: бальные танцы + предметы ХГ, если среди спортсменов есть гимнастки
    athletes = await diary_service.linked_athletes()
    group_names = [g.name for g in await diary_service.group_names_all()
                   if any(g.group_id in s.group_ids for s in athletes)]
    names = list(BALLROOM) + ([t for t in RHYTHMIC if t not in BALLROOM] if is_rhythmic(group_names) else [])
    filters = [(ALL_TOPICS.index(t), t) for t in names if t != "Другое"]
    await callback.message.edit_text(
        leaderboard_text(rows, period, topic),
        reply_markup=kb_rating("tdiary:rating", period, this, prev, filters, key,
                               back_cb="tdiary:list", back_label="« К спортсменам"),
    )
    await callback.answer()
