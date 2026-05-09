from __future__ import annotations
import re
import logging
from typing import Optional
from bot.models import Client
from bot.utils.ids import generate_client_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

_TG_ID_COL = 3
_CREATED_AT_COL = 4
_PHONE_COL = 5


def _parse_tg_id(value) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return '7' + digits
    if len(digits) == 11 and digits[0] == '8':
        return '7' + digits[1:]
    return digits


def _row_to_client(row: dict) -> Client:
    return Client(
        client_id=str(row["client_id"]),
        name=str(row["name"]),
        tg_id=_parse_tg_id(row.get("tg_id")),
        created_at=str(row.get("created_at") or ""),
        phone=str(row["phone"]) if row.get("phone") else None,
    )


class ClientRepository(BaseRepository):
    async def get_all(self) -> list[Client]:
        return [_row_to_client(r) for r in await self._all_records()]

    async def get_by_id(self, client_id: str) -> Optional[Client]:
        for c in await self.get_all():
            if c.client_id == client_id:
                return c
        return None

    async def get_by_tg_id(self, tg_id: int) -> Optional[Client]:
        for c in await self.get_all():
            if c.tg_id == tg_id:
                return c
        return None

    async def get_by_phone(self, phone: str) -> Optional[Client]:
        norm = _normalize_phone(phone)
        if not norm:
            return None
        for c in await self.get_all():
            if _normalize_phone(c.phone or "") == norm:
                return c
        return None

    async def create(self, name: str, created_by_tg_id: int, phone: str = "") -> Client:
        existing_ids = [c.client_id for c in await self.get_all()]
        client_id = generate_client_id(existing_ids)
        created_at = now_str()
        await self._append_row([client_id, name, "", created_at, phone])
        return Client(
            client_id=client_id, name=name, tg_id=None,
            created_at=created_at, phone=phone or None,
        )

    async def set_tg_id(self, client_id: str, tg_id: int) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _TG_ID_COL, tg_id)
        return True

    async def clear_tg_id(self, client_id: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _TG_ID_COL, "")
        return True

    async def set_phone(self, client_id: str, phone: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _PHONE_COL, phone)
        return True
