from typing import Optional
from bot.models import User
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
