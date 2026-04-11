from typing import Optional
from bot.models import User
from bot.utils import generate_user_id
from .base import BaseRepository


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _row_to_user(row: dict) -> User:
    return User(
        user_id=str(row["user_id"]),
        tg_id=int(row["tg_id"]),
        is_admin=_to_bool(row.get("is_admin", False)),
        teacher_id=str(row["teacher_id"]) if row.get("teacher_id") else None,
    )


class UserRepository(BaseRepository):
    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        records = await self._all_records()
        for row in records:
            if str(row.get("tg_id", "")).strip() == str(tg_id):
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
            if str(row.get("tg_id", "")).strip() == str(tg_id):
                row_idx = i + 2
                # колонка teacher_id — 4-я (user_id, tg_id, is_admin, teacher_id)
                await self._update_cell(row_idx, 4, teacher_id)
                return True
        return False
