import logging
from typing import Optional
from bot.models import Student, StudentGroupTier
from bot.utils import generate_student_id
from .base import BaseRepository

logger = logging.getLogger(__name__)

# Колонки листа `students` (1-based):
# 1 student_id | 2 name | 3 partner_id | 4 group_id (устарело) | 5 group_tier | 6 client_id | 7 parent_tg_ids
_PARTNER_COL = 3
_TIER_COL = 5
_CLIENT_ID_COL = 6
_PARENT_TG_IDS_COL = 7


def _row_to_student(row: dict) -> Student:
    partner_raw = row.get("partner_id")
    partner_id = str(partner_raw).strip() if partner_raw else ""
    tier_raw = str(row.get("group_tier") or "").strip().lower()
    try:
        tier = StudentGroupTier(tier_raw) if tier_raw else StudentGroupTier.FULL
    except ValueError:
        tier = StudentGroupTier.FULL
    client_id_raw = row.get("client_id")
    client_id = str(client_id_raw).strip() if client_id_raw else None
    tg_ids_raw = str(row.get("parent_tg_ids") or "").strip()
    # Separator was changed from "," to "|" to avoid Google Sheets interpreting
    # comma-separated numbers as a decimal number in Russian locale.
    # Old comma-separated values are still supported for backwards compatibility.
    sep = "|" if "|" in tg_ids_raw else ","
    parent_tg_ids = [
        int(x) for x in tg_ids_raw.split(sep)
        if x.strip().lstrip("-").isdigit()
    ]
    return Student(
        student_id=str(row["student_id"]),
        name=str(row["name"]),
        partner_id=partner_id or None,
        group_ids=[],
        group_tier=tier,
        client_id=client_id or None,
        parent_tg_ids=parent_tg_ids,
    )


class StudentRepository(BaseRepository):
    async def get_all(self) -> list[Student]:
        return [_row_to_student(r) for r in await self._all_records()]

    async def get_by_id(self, student_id: str) -> Optional[Student]:
        for s in await self.get_all():
            if s.student_id == student_id:
                return s
        return None

    async def search_by_name(self, prefix: str) -> list[Student]:
        prefix_lower = prefix.lower()
        return [s for s in await self.get_all() if s.name.lower().startswith(prefix_lower)]

    async def add(self, name: str) -> Student:
        existing_ids = [s.student_id for s in await self.get_all()]
        student_id = generate_student_id(existing_ids)
        # Колонка 4 (устаревшая group_id) заполняется пустой строкой.
        await self._append_row([student_id, name, "", "", StudentGroupTier.FULL.value])
        return Student(student_id=student_id, name=name, partner_id=None, group_ids=[])

    async def update_name(self, student_id: str, name: str) -> bool:
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, 2, name)
        return True

    async def update_tier(self, student_id: str, tier: StudentGroupTier) -> bool:
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _TIER_COL, tier.value)
        return True

    async def delete(self, student_id: str) -> bool:
        # Перед удалением — разорвать пару, чтобы у бывшего партнёра
        # не осталась висячая ссылка partner_id на удалённого.
        await self.clear_partner(student_id)
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._delete_row(row_idx)
        return True

    # ─── Управление партнёрами ────────────────────────────────────────────────

    async def _write_partner(self, student_id: str, partner_id: str) -> None:
        """Низкоуровневая запись: ставит partner_id в ячейке конкретного ученика."""
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            raise ValueError(f"Student {student_id} not found")
        await self._update_cell(row_idx, _PARTNER_COL, partner_id)

    async def set_partner(self, student_id: str, partner_id: str) -> None:
        """
        Связывает двух учеников как партнёров с двусторонней симметрией.
        Если у кого-то из них был другой партнёр — очищает обратные ссылки
        у старых партнёров.

        При сбое записи делается best-effort откат: если вторая запись упала,
        первая возвращается в прежнее состояние.
        """
        if student_id == partner_id:
            raise ValueError("Ученик не может быть партнёром самому себе")

        all_students = await self.get_all()
        by_id = {s.student_id: s for s in all_students}
        a = by_id.get(student_id)
        b = by_id.get(partner_id)
        if a is None or b is None:
            raise ValueError("Один из учеников не найден")

        # Уже связаны верно — ничего не делаем.
        if a.partner_id == partner_id and b.partner_id == student_id:
            return

        a_old = a.partner_id if a.partner_id and a.partner_id != partner_id else None
        b_old = b.partner_id if b.partner_id and b.partner_id != student_id else None

        # 1. Разрываем старые связи у прежних партнёров (их обратные ссылки).
        if a_old and a_old in by_id:
            await self._write_partner(a_old, "")
        if b_old and b_old in by_id and b_old != a_old:
            await self._write_partner(b_old, "")

        # 2. Ставим A.partner_id = B. Если упадём на шаге 3 — откатим.
        prev_a = a.partner_id or ""
        await self._write_partner(student_id, partner_id)
        try:
            await self._write_partner(partner_id, student_id)
        except Exception:
            logger.error(
                "set_partner: откат — вторая запись (B→A) упала, возвращаем A.partner_id=%r",
                prev_a,
            )
            try:
                await self._write_partner(student_id, prev_a)
            except Exception as rollback_exc:
                logger.error("set_partner: откат не удался: %s", rollback_exc)
            raise

    async def set_client_id(self, student_id: str, client_id: str) -> bool:
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _CLIENT_ID_COL, client_id)
        return True

    async def clear_client_id(self, student_id: str) -> bool:
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        await self._update_cell(row_idx, _CLIENT_ID_COL, "")
        return True

    async def get_students_for_client(self, client_id: str) -> list[Student]:
        return [s for s in await self.get_all() if s.client_id == client_id]

    async def get_by_parent_tg_id(self, tg_id: int) -> list[Student]:
        return [s for s in await self.get_all() if tg_id in s.parent_tg_ids]

    async def add_parent_tg_id(self, student_id: str, tg_id: int) -> bool:
        student = await self.get_by_id(student_id)
        if student is None:
            return False
        if tg_id in student.parent_tg_ids:
            return True
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        new_ids = student.parent_tg_ids + [tg_id]
        await self._update_cell(row_idx, _PARENT_TG_IDS_COL, "|".join(str(i) for i in new_ids))
        return True

    async def remove_parent_tg_id(self, student_id: str, tg_id: int) -> bool:
        student = await self.get_by_id(student_id)
        if student is None:
            return False
        row_idx = await self._find_row_index("student_id", student_id)
        if row_idx is None:
            return False
        new_ids = [i for i in student.parent_tg_ids if i != tg_id]
        await self._update_cell(row_idx, _PARENT_TG_IDS_COL, "|".join(str(i) for i in new_ids))
        return True

    async def clear_partner(self, student_id: str) -> None:
        """Разрывает связь с обеих сторон. Безопасно вызывать для солиста."""
        student = await self.get_by_id(student_id)
        if student is None or not student.partner_id:
            return
        partner_id = student.partner_id
        # Чистим обе стороны. Если вторая запись упала — первая уже очищена,
        # висячей ссылки всё равно не останется (у partner.partner_id было student_id,
        # при ошибке педагог увидит её и повторит clear из карточки партнёра).
        await self._write_partner(student_id, "")
        try:
            await self._write_partner(partner_id, "")
        except Exception as exc:
            logger.error(
                "clear_partner: не удалось очистить обратную ссылку у %s: %s",
                partner_id, exc,
            )
            raise
