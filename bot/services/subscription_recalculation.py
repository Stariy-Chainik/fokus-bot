"""Перерасчёт абонемента по подтверждённым пропускам. Справки остаются в чате."""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import astuple, dataclass
from pathlib import Path

from bot.models import SubscriptionOverride

LESSONS_PER_SUBSCRIPTION = 8


def recalculated_amount(base: int, missed: int) -> int:
    """Округляем сумму к оплате вниз до рубля, как в примере 5500 × 5 / 8 = 3437."""
    return base * (LESSONS_PER_SUBSCRIPTION - missed) // LESSONS_PER_SUBSCRIPTION


def override_version(row: SubscriptionOverride | None) -> str:
    return f"{row.amount}:{row.created_at}" if row is not None else ""


class RecalculationConflict(Exception):
    """Справка или абонемент уже изменились после расчёта."""


@dataclass(frozen=True)
class Recalculation:
    group_id: str
    period: str
    student_id: str
    start: str
    end: str
    missed: int
    base: int
    amount: int
    override_version: str
    actor: int
    created_at: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.group_id, self.period, self.student_id


class SubscriptionRecalculations:
    """Храним финальную сумму и подтверждённые даты отдельно от обычных цен и оплат."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.revision = 0
        self._rows: tuple[Recalculation, ...] | None = None
        self._lock = asyncio.Lock()

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return sqlite3.connect(self.path, timeout=15.0)

    def _load(self) -> tuple[Recalculation, ...]:
        with closing(self._open()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS subscription_recalculations (
                id INTEGER PRIMARY KEY, group_id TEXT NOT NULL, period TEXT NOT NULL, student_id TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, missed INTEGER NOT NULL, base INTEGER NOT NULL,
                amount INTEGER NOT NULL, override_version TEXT NOT NULL, actor INTEGER NOT NULL,
                created_at TEXT NOT NULL, UNIQUE (group_id, student_id, start_date, end_date)
            )""")
            return tuple(Recalculation(*row) for row in conn.execute("""
                SELECT group_id, period, student_id, start_date, end_date, missed, base, amount,
                       override_version, actor, created_at FROM subscription_recalculations ORDER BY id
            """))

    async def rows(self) -> tuple[Recalculation, ...]:
        if self._rows is None:
            async with self._lock:
                if self._rows is None:
                    self._rows = await asyncio.to_thread(self._load)
        return self._rows

    def _insert(self, row: Recalculation, previous_count: int) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            overlap, month_count = conn.execute("""
                SELECT COALESCE(SUM(start_date <= ? AND end_date >= ?), 0), COALESCE(SUM(period = ?), 0)
                FROM subscription_recalculations WHERE group_id = ? AND student_id = ?
            """, (row.end, row.start, row.period, row.group_id, row.student_id)).fetchone()
            if overlap or month_count != previous_count:
                raise RecalculationConflict
            conn.execute("""INSERT INTO subscription_recalculations (
                group_id, period, student_id, start_date, end_date, missed, base, amount, override_version, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", astuple(row))

    async def add(self, row: Recalculation, previous_count: int) -> None:
        await self.rows()
        async with self._lock:
            write = asyncio.create_task(asyncio.to_thread(self._insert, row, previous_count))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise
            finally:
                self._rows = None
                self.revision += 1
