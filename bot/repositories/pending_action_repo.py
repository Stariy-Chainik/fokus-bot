"""Очередь решений администратора — лист `pending_actions`.

Раньше ожидающие решения жили только в сообщениях Telegram: пришёл чек или
уведомление о наличных — подтвердить можно было лишь с той кнопки. Здесь они
хранятся, поэтому очередь видна в кабинете и не теряется.

Строка закрывается, откуда бы решение ни пришло (кнопка в чате или кабинет),
так что очередь показывает только то, что действительно ждёт администратора.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .base import BaseRepository

logger = logging.getLogger(__name__)

# Колонки листа `pending_actions` (1-based):
# action_id | kind | student_id | student_name | period_month | amount | method |
# parent_addr | file_id | file_type | comment | created_at | status | decided_at | decided_by_tg_id
_STATUS_COL = 13
_DECIDED_AT_COL = 14
_DECIDED_BY_COL = 15

KIND_CASH = "cash"          # родитель сообщил об оплате наличными
KIND_RECEIPT = "receipt"    # родитель прислал чек о переводе
KIND_CHILD = "child"        # родитель просит привязать ещё одного ребёнка

OPEN = "open"
DONE = "done"
REJECTED = "rejected"


@dataclass
class PendingAction:
    action_id: str
    kind: str
    student_id: str
    student_name: str
    period_month: str
    amount: int
    method: str
    parent_addr: str          # «123» для Telegram, «m456» для MAX (parent_notifier.fmt_addr)
    file_id: str              # чек: file_id в Telegram
    file_type: str            # photo | document
    comment: str
    created_at: str
    status: str = OPEN
    decided_at: str = ""
    decided_by_tg_id: int = 0


def _row_to_action(row: dict) -> PendingAction:
    return PendingAction(
        action_id=str(row.get("action_id") or ""),
        kind=str(row.get("kind") or ""),
        student_id=str(row.get("student_id") or ""),
        student_name=str(row.get("student_name") or ""),
        period_month=str(row.get("period_month") or ""),
        amount=int(row.get("amount") or 0),
        method=str(row.get("method") or ""),
        parent_addr=str(row.get("parent_addr") or ""),
        file_id=str(row.get("file_id") or ""),
        file_type=str(row.get("file_type") or ""),
        comment=str(row.get("comment") or ""),
        created_at=str(row.get("created_at") or ""),
        status=str(row.get("status") or OPEN),
        decided_at=str(row.get("decided_at") or ""),
        decided_by_tg_id=int(row.get("decided_by_tg_id") or 0),
    )


class PendingActionRepository(BaseRepository):
    async def get_all(self) -> list[PendingAction]:
        return [_row_to_action(r) for r in await self._all_records() if r.get("action_id")]

    async def get_open(self) -> list[PendingAction]:
        return [a for a in await self.get_all() if a.status == OPEN]

    async def get_by_id(self, action_id: str) -> Optional[PendingAction]:
        return next((a for a in await self.get_all() if a.action_id == action_id), None)

    async def add(
        self, kind: str, student_id: str, student_name: str, period_month: str = "",
        amount: int = 0, method: str = "", parent_addr: str = "",
        file_id: str = "", file_type: str = "", comment: str = "",
    ) -> PendingAction:
        rows = await self.get_all()
        action_id = f"ACT-{max((int(a.action_id.split('-')[-1]) for a in rows if a.action_id[4:].isdigit()), default=0) + 1:06d}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._append_row([
            action_id, kind, student_id, student_name, period_month, amount, method,
            parent_addr, file_id, file_type, comment, now, OPEN, "", "",
        ])
        return PendingAction(action_id, kind, student_id, student_name, period_month, amount,
                             method, parent_addr, file_id, file_type, comment, now)

    async def claim(self, action_id: str, status: str, decided_by_tg_id: int = 0) -> bool:
        """Занять решение: `open` → status, один раз. False — кто-то решил раньше.

        Проверка и запись под замком листа, статус перечитывается с живого листа,
        поэтому три копии одного уведомления в чате дадут ровно одно зачисление.
        """
        async with self._locked_row(action_id=action_id) as row_idx:
            if row_idx is None:
                return False
            live = await asyncio.to_thread(self._sync_row_values, row_idx)
            current = live[_STATUS_COL - 1] if len(live) >= _STATUS_COL else ""
            if (current or OPEN) != OPEN:
                logger.info("Очередь решений: %s уже %s — повтор не проводим", action_id, current)
                return False
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self._update_cell(row_idx, _STATUS_COL, status)
            await self._update_cell(row_idx, _DECIDED_AT_COL, now)
            await self._update_cell(row_idx, _DECIDED_BY_COL, decided_by_tg_id)
        return True

    async def close(self, action_id: str, status: str, decided_by_tg_id: int = 0) -> bool:
        async with self._locked_row(action_id=action_id) as row_idx:
            if row_idx is None:
                return False
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self._update_cell(row_idx, _STATUS_COL, status)
            await self._update_cell(row_idx, _DECIDED_AT_COL, now)
            await self._update_cell(row_idx, _DECIDED_BY_COL, decided_by_tg_id)
        return True

    async def close_for_period(
        self, student_id: str, period_month: str, status: str, decided_by_tg_id: int = 0,
        kinds: tuple = (KIND_CASH, KIND_RECEIPT),
    ) -> int:
        """Закрыть все открытые оплаты ученика за месяц — решение принято в чате или кабинете."""
        closed = 0
        for a in await self.get_open():
            if a.student_id == student_id and a.period_month == period_month and a.kind in kinds:
                if await self.close(a.action_id, status, decided_by_tg_id):
                    closed += 1
        return closed
