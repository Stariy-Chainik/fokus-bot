from __future__ import annotations
import logging
from typing import Optional
from bot.models import Client
from bot.utils.ids import generate_client_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

_TG_ID_COL = 3


def _parse_tg_id(value) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


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

    async def create(self, name: str, created_by_tg_id: int, phone: str = "") -> Client:
        existing_ids = [c.client_id for c in await self.get_all()]
        client_id = generate_client_id(existing_ids)
        created_at = now_str()
        await self._append_row([client_id, name, "", created_at, phone])
        return Client(
            client_id=client_id, name=name, tg_id=None,
            created_at=created_at, phone=phone or None,
        )

    async def clear_tg_id(self, client_id: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _TG_ID_COL, "")
        return True
