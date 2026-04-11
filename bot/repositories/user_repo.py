import logging
from typing import Optional
from bot.models import User
from bot.utils import generate_user_id
from .base import BaseRepository

logger = logging.getLogger(__name__)


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _parse_tg_id(value) -> Optional[int]:
    """Парсит tg_id из ячейки — Google Sheets может вернуть float (826576855.0)."""
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _row_to_user(row: dict) -> User:
    return User(
        user_id=str(row["user_id"]),
        tg_id=_parse_tg_id(row["tg_id"]),
        is_admin=_to_bool(row.get("is_admin", False)),
        teacher_id=str(row["teacher_id"]) if row.get("teacher_id") else None,
    )


class UserRepository(BaseRepository):
    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        records = await self._all_records()
        for row in records:
            if _parse_tg_id(row.get("tg_id")) == tg_id:
                return _row_to_user(row)
        return None

    async def get_all(self) -> list[User]:
        return [_row_to_user(r) for r in await self._all_records()]

    async def add(self, tg_id: int) -> User:
        """Создаёт нового пользователя без роли."""
        existing_ids = [u.user_id for u in await self.get_all()]
        user_id = generate_user_id(existing_ids)
        await self._append_row([user_id, tg_id, False, ""])
        return User(user_id=user_id, tg_id=tg_id, is_admin=False, teacher_id=None)

    async def update_teacher_id(self, tg_id: int, teacher_id: str) -> bool:
        """Привязывает teacher_id к пользователю по его tg_id."""
        records = await self._all_records()
        for i, row in enumerate(records):
            if _parse_tg_id(row.get("tg_id")) == tg_id:
                row_idx = i + 2
                # колонка teacher_id — 4-я (user_id, tg_id, is_admin, teacher_id)
                await self._update_cell(row_idx, 4, teacher_id)
                return True
        return False

    async def delete_by_teacher_id(self, teacher_id: str) -> bool:
        """Удаляет пользователя из таблицы при удалении педагога."""
        records = await self._all_records()
        logger.info("delete_by_teacher_id: ищем teacher_id=%r в %d строках", teacher_id, len(records))
        for i, row in enumerate(records):
            val = row.get("teacher_id")
            logger.info("  строка %d: teacher_id=%r", i + 2, val)
            if str(val or "").strip() == teacher_id:
                await self._delete_row(i + 2)
                logger.info("  → удалена строка %d", i + 2)
                return True
        logger.warning("delete_by_teacher_id: строка с teacher_id=%r не найдена", teacher_id)
        return False
