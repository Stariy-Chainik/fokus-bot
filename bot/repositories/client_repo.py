from __future__ import annotations
import logging
from typing import Optional
from bot.models import Client
from bot.utils.ids import generate_client_id
from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

_TG_ID_COL = 3
_PHONE_COL = 5
_EMAIL_COL = 6
_MAX_ID_COL = 7


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
        email=str(row["email"]).strip() if row.get("email") else None,
        max_id=_parse_tg_id(row.get("max_id")),
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

    async def get_by_max_id(self, max_id: int) -> Optional[Client]:
        for c in await self.get_all():
            if c.max_id == max_id:
                return c
        return None

    async def get_by_addr(self, addr) -> Optional[Client]:
        platform, ident = addr
        return await (self.get_by_max_id(ident) if platform == "max" else self.get_by_tg_id(ident))

    async def create(
        self, name: str, created_by_tg_id: int, phone: str = "",
        tg_id: Optional[int] = None, max_id: Optional[int] = None,
    ) -> Client:
        existing_ids = [c.client_id for c in await self.get_all()]
        client_id = generate_client_id(existing_ids)
        created_at = now_str()
        await self._append_row([
            client_id, name, str(tg_id) if tg_id else "", created_at, phone, "",
            str(max_id) if max_id else "",
        ])
        return Client(
            client_id=client_id, name=name, tg_id=tg_id,
            created_at=created_at, phone=phone or None, max_id=max_id,
        )

    async def set_max_id(self, client_id: str, max_id: Optional[int]) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _MAX_ID_COL, max_id or "")
        return True

    async def update_email(self, client_id: str, email: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _EMAIL_COL, email)
        return True

    async def update_phone(self, client_id: str, phone: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _PHONE_COL, phone)
        return True

    async def clear_tg_id(self, client_id: str) -> bool:
        row_idx = await self._find_row_index("client_id", client_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _TG_ID_COL, "")
        return True
