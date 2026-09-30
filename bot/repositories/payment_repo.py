from typing import Optional
from bot.models import StudentPeriodPayment
from bot.models.enums import PaymentStatus
from bot.utils.ids import generate_payment_id
from bot.utils import now_str
from .base import BaseRepository


def _row_to_payment(row: dict) -> StudentPeriodPayment:
    confirmed_by = row.get("confirmed_by_tg_id")
    return StudentPeriodPayment(
        payment_id=str(row["payment_id"]),
        student_id=str(row["student_id"]),
        student_name=str(row["student_name"]),
        period_month=str(row["period_month"]),
        total_amount=int(row.get("total_amount") or 0),
        status=PaymentStatus(str(row.get("status") or "pending")),
        paid_at=str(row["paid_at"]) if row.get("paid_at") else None,
        confirmed_by_tg_id=(
            int(confirmed_by) if confirmed_by is not None and str(confirmed_by) != "" else None
        ),
        comment=str(row["comment"]) if row.get("comment") else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        teacher_id=str(row.get("teacher_id") or ""),
        teacher_name=str(row.get("teacher_name") or ""),
        payment_method=str(row.get("payment_method") or ""),
        lesson_ids=str(row.get("lesson_ids") or ""),
    )


class PaymentRepository(BaseRepository):
    async def get_all(self) -> list[StudentPeriodPayment]:
        return [_row_to_payment(r) for r in await self._all_records()]

    async def get_by_student_and_period(
        self, student_id: str, period_month: str
    ) -> list[StudentPeriodPayment]:
        """Все счета ученика за период (по одному на педагога)."""
        return [
            p for p in await self.get_all()
            if p.student_id == student_id and p.period_month == period_month
        ]

    async def get_rows_for(
        self, student_id: str, period_month: str, teacher_id: str,
    ) -> list[StudentPeriodPayment]:
        """Все строки связки (ученик, педагог, месяц): оплаты (paid) и остаток (pending)."""
        return [
            p for p in await self.get_all()
            if p.student_id == student_id and p.period_month == period_month
            and p.teacher_id == teacher_id
        ]

    async def get_by_student_period_teacher(
        self, student_id: str, period_month: str, teacher_id: str,
    ) -> Optional[StudentPeriodPayment]:
        """Строка-остаток (pending) связки или None. Оплаченные строки см. get_rows_for."""
        for p in await self.get_rows_for(student_id, period_month, teacher_id):
            if p.status != PaymentStatus.PAID:
                return p
        return None

    async def get_by_id(self, payment_id: str) -> Optional[StudentPeriodPayment]:
        for p in await self.get_all():
            if p.payment_id == payment_id:
                return p
        return None

    async def get_existing_ids(self) -> list[str]:
        return [p.payment_id for p in await self.get_all()]

    async def add_new(self, payment: StudentPeriodPayment) -> StudentPeriodPayment:
        """Добавить строку со свежим номером. Номер выдаётся под замком листа: два параллельных
        `ledger_for` (два экрана, два ученика) иначе читали один max и получали один PAY-номер
        на двоих — подтверждение по номеру попадало в чужую строку (Архипова/Великая, 27.09.2026)."""
        async with self._sheet_lock():
            payment.payment_id = generate_payment_id(await self.get_existing_ids())
            await self.add(payment)
        return payment

    async def add(self, payment: StudentPeriodPayment) -> StudentPeriodPayment:
        await self._append_row([
            payment.payment_id,
            payment.student_id,
            payment.student_name,
            payment.period_month,
            payment.total_amount,
            payment.status.value,
            payment.paid_at or "",
            "" if payment.confirmed_by_tg_id is None else payment.confirmed_by_tg_id,
            payment.comment or "",
            payment.created_at,
            payment.updated_at,
            payment.teacher_id,
            payment.teacher_name,
            payment.payment_method,
            payment.lesson_ids,
        ])
        return payment

    @staticmethod
    def _key(payment_id: str, student_id: str) -> dict:
        """Ключ строки: номер и, если известен, ученик — чтобы задвоенный номер не увёл запись к чужой строке."""
        return {"payment_id": payment_id, **({"student_id": student_id} if student_id else {})}

    async def set_lesson_ids(self, payment_id: str, lesson_ids: str, student_id: str = "") -> bool:
        """Записать занятия оплаты (или намерение плательщика у строки-остатка)."""
        async with self._locked_row(**self._key(payment_id, student_id)) as row_idx:
            if row_idx is None:
                return False
            await self._update_cells(row_idx, {15: lesson_ids, 11: now_str()})
        return True

    async def update_amount(self, payment_id: str, new_amount: int, student_id: str = "") -> bool:
        ts_now = now_str()
        async with self._locked_row(**self._key(payment_id, student_id)) as row_idx:
            if row_idx is None:
                return False
            # кеш правится на месте (_patch_cache): полный сброс листа заставлял следующий запрос
            # скачивать все ~1000 строк оплат заново (0,4 с на каждую правку остатка)
            await self._update_cells(row_idx, {5: new_amount, 11: ts_now})   # total_amount, updated_at
            return True

    async def confirm_all_for_period(
        self, student_id: str, period_month: str, confirmed_by_tg_id: int,
        payment_method: str = "admin_manual",
    ) -> int:
        """Подтверждает все PENDING счета ученика за период. Возвращает кол-во обновлённых."""
        records = await self._all_records()
        ts_now = now_str()
        count = 0
        pending_ids = [
            str(row.get("payment_id")) for row in records
            if not (str(row.get("student_id")) != student_id
                    or str(row.get("period_month")) != period_month
                    or str(row.get("status") or "pending") == PaymentStatus.PAID.value
                    or int(float(row.get("total_amount") or 0)) <= 0)  # остаток 0 — платить нечего
        ]
        for payment_id in pending_ids:
            async with self._locked_row(payment_id=payment_id) as row_idx:
                if row_idx is None:
                    continue
                await self._update_cells(row_idx, {6: PaymentStatus.PAID.value, 7: ts_now, 8: confirmed_by_tg_id,
                                                   11: ts_now, 14: payment_method})
                count += 1
        if count:
            self._invalidate_cache()
        return count

    async def confirm(
        self, payment_id: str, confirmed_by_tg_id: int,
        payment_method: str = "admin_manual", student_id: str = "",
    ) -> bool:
        """Подтверждает оплату и фиксирует её точный способ."""
        ts_now = now_str()
        async with self._locked_row(**self._key(payment_id, student_id)) as row_idx:
            if row_idx is None:
                return False
            # status, paid_at, confirmed_by_tg_id, updated_at, payment_method — одним запросом
            await self._update_cells(row_idx, {6: PaymentStatus.PAID.value, 7: ts_now, 8: confirmed_by_tg_id,
                                               11: ts_now, 14: payment_method})
            return True
