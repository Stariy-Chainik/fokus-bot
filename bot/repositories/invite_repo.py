from __future__ import annotations
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from bot.models import ClientInviteCode
from bot.models.enums import InviteCodeStatus
from bot.utils.ids import generate_invite_code_id
from .base import BaseRepository

logger = logging.getLogger(__name__)

_STATUS_COL = 7   # 1-based: code_id,code,client_id,created_by,created_at,expires_at,status,used_at,used_by_tg_id


def _parse_tg_id(value) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _row_to_code(row: dict) -> ClientInviteCode:
    return ClientInviteCode(
        code_id=str(row["code_id"]),
        code=str(row["code"]),
        client_id=str(row["client_id"]),
        created_by=_parse_tg_id(row.get("created_by")) or 0,
        created_at=str(row.get("created_at") or ""),
        expires_at=str(row.get("expires_at") or ""),
        status=str(row.get("status") or InviteCodeStatus.ACTIVE.value),
        used_at=str(row["used_at"]) if row.get("used_at") else None,
        used_by_tg_id=_parse_tg_id(row.get("used_by_tg_id")),
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class ClientInviteCodeRepository(BaseRepository):
    async def get_all(self) -> list[ClientInviteCode]:
        return [_row_to_code(r) for r in await self._all_records()]

    async def get_by_code(self, code: str) -> Optional[ClientInviteCode]:
        for c in await self.get_all():
            if c.code == code:
                return c
        return None

    async def get_active_for_client(self, client_id: str) -> Optional[ClientInviteCode]:
        now = _now_utc()
        for c in await self.get_all():
            if c.client_id != client_id:
                continue
            if c.status != InviteCodeStatus.ACTIVE.value:
                continue
            try:
                expires = datetime.fromisoformat(c.expires_at.replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue
            if expires > now:
                return c
        return None

    async def create(self, client_id: str, created_by: int, ttl_hours: int) -> ClientInviteCode:
        all_codes = await self.get_all()
        # Отзываем все active коды этого клиента
        for c in all_codes:
            if c.client_id == client_id and c.status == InviteCodeStatus.ACTIVE.value:
                row_idx = await self._find_row_index("code_id", c.code_id)
                if row_idx is not None:
                    await self._update_cell(row_idx, _STATUS_COL, InviteCodeStatus.REVOKED.value)

        existing_ids = [c.code_id for c in all_codes]
        code_id = generate_invite_code_id(existing_ids)
        code = str(secrets.randbelow(900000) + 100000)  # 6 цифр 100000-999999
        now = _now_utc()
        expires_at = now + timedelta(hours=ttl_hours)
        await self._append_row([
            code_id, code, client_id, created_by,
            _fmt(now), _fmt(expires_at),
            InviteCodeStatus.ACTIVE.value, "", "",
        ])
        return ClientInviteCode(
            code_id=code_id,
            code=code,
            client_id=client_id,
            created_by=created_by,
            created_at=_fmt(now),
            expires_at=_fmt(expires_at),
            status=InviteCodeStatus.ACTIVE.value,
        )

    async def mark_used(self, code_id: str, tg_id: int) -> bool:
        row_idx = await self._find_row_index("code_id", code_id)
        if row_idx is None:
            return False
        now_s = _fmt(_now_utc())
        # Обновляем статус (col 7), used_at (col 8), used_by_tg_id (col 9)
        await self._update_cell(row_idx, 7, InviteCodeStatus.USED.value)
        await self._update_cell(row_idx, 8, now_s)
        await self._update_cell(row_idx, 9, tg_id)
        return True
