from __future__ import annotations
import logging

from bot.models import StudentPeriodPayment, Student
from bot.models.enums import PaymentStatus
from bot.utils import generate_payment_id, now_str
from bot.repositories import (
    PaymentRepository, LessonRepository, TeacherRepository,
    TeacherPeriodSubmissionRepository,
)
from .billing_service import build_billing_rows

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        lesson_repo: LessonRepository,
        teacher_repo: TeacherRepository,
        submission_repo: TeacherPeriodSubmissionRepository,
    ) -> None:
        self._payment_repo = payment_repo
        self._lesson_repo = lesson_repo
        self._teacher_repo = teacher_repo
        self._submission_repo = submission_repo

    async def compute_bills_for_student_period(
        self, student_id: str, period_month: str,
    ) -> dict[str, dict]:
        """
        On-demand расчёт счёта ученика за период.
        Возвращает dict[teacher_id] -> {name, total, items: list[Billing-like dicts]}.
        """
        lessons = await self._lesson_repo.get_by_student_and_period(student_id, period_month)
        teachers_cache: dict[str, object] = {}
        result: dict[str, dict] = {}
        for ls in lessons:
            teacher = teachers_cache.get(ls.teacher_id)
            if teacher is None:
                teacher = await self._teacher_repo.get_by_id(ls.teacher_id)
                if teacher is None:
                    logger.warning("Педагог %s не найден для занятия %s", ls.teacher_id, ls.lesson_id)
                    continue
                teachers_cache[ls.teacher_id] = teacher
            for b in build_billing_rows(ls, teacher):
                if b.student_id != student_id:
                    continue
                agg = result.setdefault(b.teacher_id, {
                    "name": b.teacher_name, "total": 0, "items": [],
                })
                agg["total"] += b.amount
                agg["items"].append(b)
        return result

    async def get_or_create_invoices_for_student_period(
        self, student: Student, period_month: str,
    ) -> list[StudentPeriodPayment]:
        """Возвращает (создаёт при необходимости) по одному счёту на каждого педагога,
        у которого есть индивидуальные занятия с этим учеником за период."""
        bills = await self.compute_bills_for_student_period(student.student_id, period_month)
        if not bills:
            return []

        invoices: list[StudentPeriodPayment] = []
        for teacher_id, agg in bills.items():
            existing = await self._payment_repo.get_by_student_period_teacher(
                student.student_id, period_month, teacher_id,
            )
            if existing:
                # Сумма могла измениться (добавили/удалили занятие) — refresh, если ещё не оплачен
                if existing.status != PaymentStatus.PAID and existing.total_amount != agg["total"]:
                    logger.info(
                        "Сумма счёта %s изменилась: %d → %d (refresh)",
                        existing.payment_id, existing.total_amount, agg["total"],
                    )
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

    async def confirm_payment(
        self, payment_id: str, confirmed_by_tg_id: int, comment: str | None = None,
    ) -> bool:
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
        ok = await self._payment_repo.confirm(payment_id, confirmed_by_tg_id, comment=comment)
        if ok:
            logger.info("Счёт %s подтверждён", payment_id)
        return ok

    async def teachers_not_submitted(
        self, teacher_ids: list[str], period_month: str,
    ) -> list[str]:
        """Из списка teacher_id возвращает тех, кто ещё не сдал период."""
        not_submitted: list[str] = []
        for tid in teacher_ids:
            sub = await self._submission_repo.get_by_teacher_and_period(tid, period_month)
            if sub is None:
                not_submitted.append(tid)
        return not_submitted
