from typing import Optional
from bot.models import Billing
from bot.utils import generate_billing_id, now_str
from .base import BaseRepository


def _row_to_billing(row: dict) -> Billing:
    return Billing(
        billing_id=str(row["billing_id"]),
        lesson_id=str(row["lesson_id"]),
        student_id=str(row["student_id"]),
        student_name=str(row["student_name"]),
        teacher_id=str(row["teacher_id"]),
        teacher_name=str(row["teacher_name"]),
        date=str(row["date"]),
        duration_min=int(row["duration_min"]),
        amount=int(row.get("amount") or 0),
        period_month=str(row["period_month"]),
        payment_id=str(row["payment_id"]) if row.get("payment_id") else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class BillingRepository(BaseRepository):
    async def get_all(self) -> list[Billing]:
        return [_row_to_billing(r) for r in await self._all_records()]

    async def get_by_lesson(self, lesson_id: str) -> list[Billing]:
        return [b for b in await self.get_all() if b.lesson_id == lesson_id]

    async def get_by_student_and_period(self, student_id: str, period_month: str) -> list[Billing]:
        return [
            b for b in await self.get_all()
            if b.student_id == student_id and b.period_month == period_month
        ]

    async def get_by_teacher_and_period(self, teacher_id: str, period_month: str) -> list[Billing]:
        return [
            b for b in await self.get_all()
            if b.teacher_id == teacher_id and b.period_month == period_month
        ]

    async def get_existing_ids(self) -> list[str]:
        return [b.billing_id for b in await self.get_all()]

    async def add(self, billing: Billing) -> Billing:
        await self._append_row([
            billing.billing_id,
            billing.lesson_id,
            billing.student_id,
            billing.student_name,
            billing.teacher_id,
            billing.teacher_name,
            billing.date,
            billing.duration_min,
            billing.amount,
            billing.period_month,
            billing.payment_id or "",
            billing.created_at,
            billing.updated_at,
        ])
        return billing

    async def delete_by_lesson(self, lesson_id: str) -> int:
        """
        Физически удаляет все billing-строки по lesson_id.
        Итерируем снизу вверх — индексы выше не смещаются при удалении нижних строк.
        """
        records = await self._all_records()
        deleted = 0
        for i in range(len(records) - 1, -1, -1):
            if str(records[i].get("lesson_id")) == lesson_id:
                await self._delete_row(i + 2)
                deleted += 1
        return deleted

    async def set_payment_id_for_period(self, student_id: str, period_month: str, payment_id: str) -> int:
        """Проставляет payment_id всем billing-строкам ученика за период."""
        records = await self._all_records()
        ts_now = now_str()
        updated = 0
        for i, row in enumerate(records):
            if (str(row.get("student_id")) == student_id
                    and str(row.get("period_month")) == period_month
                    and not row.get("payment_id")):
                row_idx = i + 2
                await self._update_cell(row_idx, 11, payment_id)   # col 11 = payment_id
                await self._update_cell(row_idx, 13, ts_now)        # col 13 = updated_at
                updated += 1
        return updated

    async def delete_all(self) -> None:
        """Очищает весь лист billing (заголовки остаются). Для пересборки."""
        records = await self._all_records()
        for i in range(len(records) - 1, -1, -1):
            await self._delete_row(i + 2)
