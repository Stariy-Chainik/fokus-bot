"""Лист `activity_log` — лента изменений администратора (см. bot/services/activity.py).

Колонки: ts · kind · actor (tg_id, 0 — система/ЮКасса) · text · ref (id сущности).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .base import BaseRepository

HEADER = ["ts", "kind", "actor", "text", "ref"]


@dataclass(frozen=True)
class ActivityEvent:
    ts: str
    kind: str
    actor: int
    text: str
    ref: str = ""


def _row_to_event(row: dict) -> ActivityEvent:
    return ActivityEvent(
        ts=str(row.get("ts") or ""), kind=str(row.get("kind") or ""),
        actor=int(row.get("actor") or 0), text=str(row.get("text") or ""), ref=str(row.get("ref") or ""),
    )


class ActivityLogRepository(BaseRepository):
    async def get_all(self) -> list[ActivityEvent]:
        return [_row_to_event(r) for r in await self._all_records() if r.get("ts")]

    async def since(self, ts_from: str) -> list[ActivityEvent]:
        """События с ts >= ts_from («YYYY-MM-DD…»), новые первыми."""
        return sorted((e for e in await self.get_all() if e.ts >= ts_from), key=lambda e: e.ts, reverse=True)

    async def add(self, kind: str, text: str, actor: int = 0, ref: str = "") -> ActivityEvent:
        event = ActivityEvent(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind, actor, text, ref)
        await self._append_row([event.ts, event.kind, event.actor, event.text, event.ref])
        return event
