"""Лист parent_feedback: обращения родителей к руководителю студии (решение владельца 11.11.2026).

Родитель пишет из кабинета или бота MAX («💬 Написать руководителю»): пожелание, замечание, благодарность
или вопрос. Сообщение уходит только руководителю в Telegram (bot/services/parent_feedback.py), здесь — журнал
обращений и ответов, чтобы ничего не потерялось и родитель видел историю в кабинете.
Лист создаёт scripts/setup_parent_feedback.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import gspread

from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

HEADER = ["feedback_id", "ts", "student_id", "parent_addr", "parent_name", "kind", "text", "status",
          "reply", "replied_at", "replied_by"]
STATUS_NEW, STATUS_ANSWERED = "new", "answered"


def _int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


@dataclass(frozen=True)
class ParentFeedback:
    feedback_id: str          # FB-XXXXXX
    ts: str                   # YYYY-MM-DD HH:MM:SS
    student_id: str
    parent_addr: str          # «123» — Telegram, «m456» — MAX (parent_notifier.fmt_addr)
    parent_name: str
    kind: str                 # wish | remark | thanks | question
    text: str
    status: str = STATUS_NEW
    reply: str = ""
    replied_at: str = ""
    replied_by: int = 0       # tg_id ответившего

    @property
    def answered(self) -> bool:
        return self.status == STATUS_ANSWERED


def _row(r: dict) -> ParentFeedback:
    return ParentFeedback(
        feedback_id=str(r.get("feedback_id") or ""), ts=str(r.get("ts") or ""),
        student_id=str(r.get("student_id") or ""), parent_addr=str(r.get("parent_addr") or ""),
        parent_name=str(r.get("parent_name") or ""), kind=str(r.get("kind") or ""), text=str(r.get("text") or ""),
        status=str(r.get("status") or STATUS_NEW), reply=str(r.get("reply") or ""),
        replied_at=str(r.get("replied_at") or ""), replied_by=_int(r.get("replied_by")),
    )


class ParentFeedbackRepository(BaseRepository):
    """Лист parent_feedback (scripts/setup_parent_feedback.py)."""

    async def get_all(self) -> list[ParentFeedback]:
        try:
            records = await self._all_records()
        except gspread.WorksheetNotFound:          # лист ещё не создан — обращения недоступны, экраны работают
            logger.warning("Лист %s не найден — обращения родителей недоступны (scripts/setup_parent_feedback.py)",
                           self._sheet_name)
            return []
        return [_row(r) for r in records if r.get("feedback_id")]

    async def get_by_id(self, feedback_id: str) -> ParentFeedback | None:
        return next((r for r in await self.get_all() if r.feedback_id == feedback_id), None)

    async def get_for_parent(self, parent_addr: str) -> list[ParentFeedback]:
        """Обращения этого родителя, свежие первыми."""
        return sorted((r for r in await self.get_all() if r.parent_addr == parent_addr),
                      key=lambda r: r.ts, reverse=True)

    async def add(self, student_id: str, parent_addr: str, parent_name: str, kind: str, text: str) -> ParentFeedback:
        from bot.utils.ids import _next_id
        async with self._sheet_lock():                  # номер под замком листа
            existing = [r.feedback_id for r in await self.get_all()]
            row = ParentFeedback(_next_id("FB", 6, existing), now_str(), student_id, parent_addr, parent_name,
                                 kind, text)
            await self._append_row([row.feedback_id, row.ts, row.student_id, row.parent_addr, row.parent_name,
                                    row.kind, row.text, row.status, "", "", ""])
        logger.info("Обращение %s: %s от %s (%s)", row.feedback_id, kind, parent_addr, student_id)
        return row

    async def set_reply(self, feedback_id: str, reply: str, replied_by: int) -> bool:
        async with self._locked_row(feedback_id=feedback_id) as idx:
            if idx is None:
                return False
            await self._update_cells(idx, {8: STATUS_ANSWERED, 9: reply, 10: now_str(), 11: replied_by}, raw=True)
            return True
