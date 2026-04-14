import logging

from bot.models import StudentPeriodPayment, Student
from bot.models.enums import PaymentStatus
from bot.utils import generate_payment_id, now_str
from bot.repositories import BillingRepository, PaymentRepository

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, billing_repo: BillingRepository, payment_repo: PaymentRepository) -> None:
        self._billing_repo = billing_repo
        self._payment_repo = payment_repo

    async def get_or_create_invoices_for_student_period(
        self, student: Student, period_month: str,
    ) -> list[StudentPeriodPayment]:
        """Возвращает (создаёт при необходимости) по одному счёту на каждого педагога,
        у которого есть billing-строки этого ученика за период."""
        billing_rows = await self._billing_repo.get_by_student_and_period(
            student.student_id, period_month,
        )
        if not billing_rows:
            return []
        by_teacher: dict[str, dict] = {}
        for b in billing_rows:
            agg = by_teacher.setdefault(b.teacher_id, {"name": b.teacher_name, "total": 0})
            agg["total"] += b.amount
            agg["name"] = b.teacher_name  # последнее имя

        invoices: list[StudentPeriodPayment] = []
        for teacher_id, agg in by_teacher.items():
            existing = await self._payment_repo.get_by_student_period_teacher(
                student.student_id, period_month, teacher_id,
            )
            if existing:
                invoices.append(existing)
                continue
            now = now_str()
            existing_ids = await self._payment_repo.get_existing_ids()
            payment_id = generate_payment_id(existing_ids)
            payment = StudentPeriodPayment(
                payment_id=payment_id,
                student_id=student.student_id,
                student_name=student.name,
                period_month=period_month,
                total_amount=agg["total"],
                status=PaymentStatus.PENDING,
                paid_at=None,
                confirmed_by_tg_id=None,
                comment=None,
                created_at=now,
                updated_at=now,
                teacher_id=teacher_id,
                teacher_name=agg["name"],
            )
            await self._payment_repo.add(payment)
            logger.info(
                "Создан счёт %s student=%s teacher=%s период=%s сумма=%d",
                payment_id, student.student_id, teacher_id, period_month, agg["total"],
            )
            invoices.append(payment)
        return invoices

    async def confirm_payment(self, payment_id: str, confirmed_by_tg_id: int) -> bool:
        """
        Подтверждает оплату:
        1. Ставит status=paid в student_period_payments
        2. Проставляет payment_id в billing-строки ученика за период
           (только у педагога, к которому привязан этот счёт)
        """
        payment = next(
            (p for p in await self._payment_repo.get_all() if p.payment_id == payment_id),
            None,
        )
        if payment is None:
            logger.warning("Счёт %s не найден при подтверждении", payment_id)
            return False

        if payment.status == PaymentStatus.PAID:
            logger.warning("Повторное подтверждение счёта %s — игнорируем", payment_id)
            return False

        ok = await self._payment_repo.confirm(payment_id, confirmed_by_tg_id)
        if ok:
            updated = await self._billing_repo.set_payment_id_for_period(
                payment.student_id, payment.period_month, payment_id,
                teacher_id=payment.teacher_id or None,
            )
            logger.info("Счёт %s подтверждён, billing-строк обновлено: %d", payment_id, updated)
        return ok

    async def check_teacher_period_closed(
        self, teacher_id: str, period_month: str
    ) -> tuple[bool, int, int]:
        """
        Проверяет, все ли billing-строки педагога за период имеют payment_id.
        Возвращает (all_paid, paid_count, total_count).
        """
        rows = await self._billing_repo.get_by_teacher_and_period(teacher_id, period_month)
        total = len(rows)
        paid = sum(1 for b in rows if b.payment_id)
        return paid == total and total > 0, paid, total
