import logging
from typing import Optional
from bot.models import Student
from bot.utils import generate_student_id
from .base import BaseRepository

logger = logging.getLogger(__name__)

# Колонка partner_id в листе `students` (1-based). Порядок: student_id, name, partner_id.
_PARTNER_COL = 3


def _row_to_student(row: dict) -> Student:
    partner_raw = row.get("partner_id")
    partner_id = str(partner_raw).strip() if partner_raw else ""
    return Student(
        student_id=str(row["student_id"]),
        name=str(row["name"]),
        partner_id=partner_id or None,
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
        await self._append_row([student_id, name, ""])
        return Student(student_id=student_id, name=name, partner_id=None)

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
