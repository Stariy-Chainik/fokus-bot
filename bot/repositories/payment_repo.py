from typing import Optional
from bot.models import StudentPeriodPayment
from bot.models.enums import PaymentStatus
from bot.utils import generate_payment_id, now_str
from .base import BaseRepository


def _row_to_payment(row: dict) -> StudentPeriodPayment:
    return StudentPeriodPayment(
        payment_id=str(row["payment_id"]),
        student_id=str(row["student_id"]),
        student_name=str(row["student_name"]),
        period_month=str(row["period_month"]),
        total_amount=int(row.get("total_amount") or 0),
        status=PaymentStatus(str(row.get("status", "pending"))),
        paid_at=str(row["paid_at"]) if row.get("paid_at") else None,
        confirmed_by_tg_id=int(row["confirmed_by_tg_id"]) if row.get("confirmed_by_tg_id") else None,
        comment=str(row["comment"]) if row.get("comment") else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class PaymentRepository(BaseRepository):
    async def get_all(self) -> list[StudentPeriodPayment]:
        return [_row_to_payment(r) for r in await self._all_records()]

    async def get_by_student_and_period(
        self, student_id: str, period_month: str
    ) -> Optional[StudentPeriodPayment]:
        for p in await self.get_all():
            if p.student_id == student_id and p.period_month == period_month:
                return p
        return None

    async def get_by_student(self, student_id: str) -> list[StudentPeriodPayment]:
        return [p for p in await self.get_all() if p.student_id == student_id]

    async def get_existing_ids(self) -> list[str]:
        return [p.payment_id for p in await self.get_all()]

    async def add(self, payment: StudentPeriodPayment) -> StudentPeriodPayment:
        await self._append_row([
            payment.payment_id,
            payment.student_id,
            payment.student_name,
            payment.period_month,
            payment.total_amount,
            payment.status.value,
            payment.paid_at or "",
            payment.confirmed_by_tg_id or "",
            payment.comment or "",
            payment.created_at,
            payment.updated_at,
        ])
        return payment

    async def confirm(self, payment_id: str, confirmed_by_tg_id: int) -> bool:
        """Подтверждает оплату: status=paid, paid_at=now, confirmed_by_tg_id."""
        records = await self._all_records()
        ts_now = now_str()
        for i, row in enumerate(records):
            if str(row.get("payment_id")) == payment_id:
                row_idx = i + 2
                await self._update_cell(row_idx, 6, PaymentStatus.PAID.value)  # status
                await self._update_cell(row_idx, 7, ts_now)                     # paid_at
                await self._update_cell(row_idx, 8, confirmed_by_tg_id)         # confirmed_by_tg_id
                await self._update_cell(row_idx, 11, ts_now)                    # updated_at
                return True
        return False
