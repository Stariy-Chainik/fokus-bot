from __future__ import annotations
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from bot.models import StudentInviteCode
from bot.models.enums import InviteStatus
from bot.utils import generate_invite_id, now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

# Колонки листа `student_invite_codes` (1-based):
# invite_id | code | student_id | created_at | created_by_tg_id |
# expires_at | used_at | used_by_tg_id | status
_CODE_COL = 2
_USED_AT_COL = 7
_USED_BY_COL = 8
_STATUS_COL = 9


def _parse_int(value) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _row_to_invite(row: dict) -> StudentInviteCode:
    used_by = _parse_int(row.get("used_by_tg_id")) if row.get("used_by_tg_id") else None
    return StudentInviteCode(
        invite_id=str(row["invite_id"]),
        code=str(row.get("code") or "").zfill(6),
        student_id=str(row.get("student_id") or ""),
        created_at=str(row.get("created_at") or ""),
        created_by_tg_id=_parse_int(row.get("created_by_tg_id")) or 0,
        expires_at=str(row["expires_at"]) if row.get("expires_at") else None,
        used_at=str(row["used_at"]) if row.get("used_at") else None,
        used_by_tg_id=used_by,
        status=InviteStatus(str(row.get("status") or "active")),
    )


class StudentInviteRepository(BaseRepository):
    async def get_all(self) -> list[StudentInviteCode]:
        return [_row_to_invite(r) for r in await self._all_records()]

    async def get_by_id(self, invite_id: str) -> Optional[StudentInviteCode]:
        for inv in await self.get_all():
            if inv.invite_id == invite_id:
                return inv
        return None

    async def get_by_code(self, code: str) -> Optional[StudentInviteCode]:
        """Возвращает активный (неистёкший, неиспользованный, неотозванный) код."""
        now = datetime.now()
        for inv in await self.get_all():
            if inv.code != code:
                continue
            if inv.status != InviteStatus.ACTIVE:
                continue
            if inv.expires_at:
                try:
                    exp = datetime.strptime(inv.expires_at, "%Y-%m-%d %H:%M:%S")
                    if exp < now:
                        continue
                except ValueError:
                    pass
            return inv
        return None

    async def list_active_for_student(self, student_id: str) -> list[StudentInviteCode]:
        return [
            inv for inv in await self.get_all()
            if inv.student_id == student_id and inv.status == InviteStatus.ACTIVE
        ]

    async def _existing_ids(self) -> list[str]:
        return [inv.invite_id for inv in await self.get_all()]

    async def _active_codes(self) -> set[str]:
        return {
            inv.code for inv in await self.get_all()
            if inv.status == InviteStatus.ACTIVE
        }

    async def generate_code(
        self, student_id: str, created_by_tg_id: int, ttl_hours: int = 24,
    ) -> StudentInviteCode:
        """Создаёт новый 6-значный код для ученика.

        Предварительно отзывает все активные коды того же ученика —
        «последний выпущенный код — единственный действующий».
        """
        # Отозвать старые активные коды.
        for old in await self.list_active_for_student(student_id):
            await self.revoke(old.invite_id)

        active = await self._active_codes()
        code: Optional[str] = None
        for _ in range(20):
            candidate = f"{secrets.randbelow(1_000_000):06d}"
            if candidate not in active:
                code = candidate
                break
        if code is None:
            # Крайне маловероятно при ≪1_000_000 активных кодов; бросаем ошибку.
            raise RuntimeError("Не удалось подобрать уникальный код приглашения")

        now = now_str()
        expires_at = ""
        if ttl_hours and ttl_hours > 0:
            expires_at = (datetime.now() + timedelta(hours=ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")

        invite_id = generate_invite_id(await self._existing_ids())
        await self._append_row([
            invite_id, code, student_id, now, created_by_tg_id,
            expires_at, "", "", InviteStatus.ACTIVE.value,
        ])
        logger.info(
            "Создан код привязки %s (code=%s) student=%s by=%s",
            invite_id, code, student_id, created_by_tg_id,
        )
        return StudentInviteCode(
            invite_id=invite_id,
            code=code,
            student_id=student_id,
            created_at=now,
            created_by_tg_id=created_by_tg_id,
            expires_at=expires_at or None,
            status=InviteStatus.ACTIVE,
        )

    async def mark_used(self, invite_id: str, used_by_tg_id: int) -> bool:
        row_idx = await self._find_row_index("invite_id", invite_id)
        if row_idx is None:
            return False
        now = now_str()
        await self._update_cell(row_idx, _STATUS_COL, InviteStatus.USED.value)
        await self._update_cell(row_idx, _USED_AT_COL, now)
        await self._update_cell(row_idx, _USED_BY_COL, used_by_tg_id)
        logger.info("Код %s помечен использованным (tg=%s)", invite_id, used_by_tg_id)
        return True

    async def revoke(self, invite_id: str) -> bool:
        row_idx = await self._find_row_index("invite_id", invite_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _STATUS_COL, InviteStatus.REVOKED.value)
        logger.info("Код %s отозван", invite_id)
        return True
