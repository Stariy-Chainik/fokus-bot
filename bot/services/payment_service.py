from __future__ import annotations
import logging
import uuid

from bot.models import StudentPeriodPayment, Student
from bot.models.enums import PaymentStatus, GroupBillingMode, LessonType
from bot.utils import generate_payment_id, now_str
from bot.repositories import (
    PaymentRepository, LessonRepository, TeacherRepository,
)
from .billing_service import build_billing_rows

logger = logging.getLogger(__name__)

# Ключ «педагога» для абонементного начисления в счетах/долгах: SUB:{group_id}.
# Абонемент — продукт группы, а не педагога, но модель инвойса требует teacher_id;
# синтетический ключ хранится в той же строковой колонке.
SUBSCRIPTION_KEY_PREFIX = "SUB:"


class PaymentService:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        lesson_repo: LessonRepository,
        teacher_repo: TeacherRepository,
        group_repo=None,
        student_group_repo=None,
    ) -> None:
        self._payment_repo = payment_repo
        self._lesson_repo = lesson_repo
        self._teacher_repo = teacher_repo
        # Опциональны (для абонементных начислений); без них подписки не считаются.
        self._group_repo = group_repo
        self._student_group_repo = student_group_repo

    async def _subscription_bills_for_student(
        self, student_id: str, period_month: str,
    ) -> dict[str, dict]:
        """Абонементные начисления ученика за период: {SUB:gid → agg}.

        Правило: группа с billing_mode=SUBSCRIPTION → фиксированная price_full
        ₽/месяц с ученика, НЕЗАВИСИМО от числа занятий; начисляется, только если
        в этом месяце у группы было хотя бы одно занятие (каникулы — не платят).
        """
        if self._group_repo is None or self._student_group_repo is None:
            return {}
        gids = await self._student_group_repo.get_groups_for_student(student_id)
        if not gids:
            return {}
        sub_groups = []
        for gid in gids:
            group = await self._group_repo.get_by_id(gid)
            if group and group.billing_mode == GroupBillingMode.SUBSCRIPTION and group.price_full > 0:
                sub_groups.append(group)
        if not sub_groups:
            return {}
        # Месяцы активности групп — одним проходом по занятиям.
        active: set[str] = set()
        sub_ids = {g.group_id for g in sub_groups}
        for ls in await self._lesson_repo.get_all():
            if (ls.type == LessonType.GROUP and ls.group_id in sub_ids
                    and ls.date[:7] == period_month):
                active.add(ls.group_id)
        result: dict[str, dict] = {}
        for group in sub_groups:
            if group.group_id not in active:
                continue
            result[f"{SUBSCRIPTION_KEY_PREFIX}{group.group_id}"] = {
                "name": f"Абонемент «{group.name}»",
                "total": group.price_full,
                "items": [],
                "subscription": True,
            }
        return result

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
        result.update(await self._subscription_bills_for_student(student_id, period_month))
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
                if existing.status != PaymentStatus.PAID and existing.total_amount != agg["total"]:
                    logger.info(
                        "Сумма счёта %s изменилась: %d → %d",
                        existing.payment_id, existing.total_amount, agg["total"],
                    )
                    await self._payment_repo.update_amount(existing.payment_id, agg["total"])
                    existing.total_amount = agg["total"]
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

    async def compute_debt_map(self, since_period: str | None = None) -> dict[str, dict[str, int]]:
        """Карта долгов по всем ученикам и периодам: student_id → {period_month → долг ₽}.

        Долг считается on-demand так же, как «К оплате» у родителя: начисления
        (build_billing_rows по всем занятиям) минус оплаченные (student, teacher,
        period) со статусом PAID. Наличие/отсутствие выставленного счёта роли
        не играет. amount=0 (абонемент) в долг не входит.

        since_period ("YYYY-MM") — учитывать только периоды >= since_period;
        None/"" — за всё время. Отсекает месяцы до внедрения учёта оплат.
        """
        lessons = await self._lesson_repo.get_all()
        teachers = {t.teacher_id: t for t in await self._teacher_repo.get_all()}

        accrued: dict[tuple[str, str, str], int] = {}  # (student, teacher, period) → ₽
        for ls in lessons:
            teacher = teachers.get(ls.teacher_id)
            if teacher is None:
                logger.warning("compute_debt_map: педагог %s не найден (занятие %s)",
                               ls.teacher_id, ls.lesson_id)
                continue
            for b in build_billing_rows(ls, teacher):
                key = (b.student_id, b.teacher_id, b.period_month)
                accrued[key] = accrued.get(key, 0) + b.amount

        # Абонементные начисления: price_full ₽/мес каждому участнику SUBSCRIPTION-группы
        # за каждый месяц, где у группы было хотя бы одно занятие.
        if self._group_repo is not None and self._student_group_repo is not None:
            sub_groups = [
                g for g in await self._group_repo.get_all()
                if g.billing_mode == GroupBillingMode.SUBSCRIPTION and g.price_full > 0
            ]
            if sub_groups:
                months_by_group: dict[str, set[str]] = {}
                for ls in lessons:
                    if ls.type == LessonType.GROUP and ls.group_id:
                        months_by_group.setdefault(ls.group_id, set()).add(ls.date[:7])
                for g in sub_groups:
                    members = await self._student_group_repo.get_students_for_group(g.group_id)
                    for period in months_by_group.get(g.group_id, ()):
                        for sid in members:
                            key = (sid, f"{SUBSCRIPTION_KEY_PREFIX}{g.group_id}", period)
                            accrued[key] = accrued.get(key, 0) + g.price_full

        paid = {
            (p.student_id, p.teacher_id, p.period_month)
            for p in await self._payment_repo.get_all()
            if p.status == PaymentStatus.PAID
        }

        debts: dict[str, dict[str, int]] = {}
        for (sid, tid, period), amount in accrued.items():
            if since_period and period < since_period:
                continue
            if amount <= 0 or (sid, tid, period) in paid:
                continue
            per_student = debts.setdefault(sid, {})
            per_student[period] = per_student.get(period, 0) + amount
        return debts

    async def confirm_payment(self, payment_id: str, confirmed_by_tg_id: int) -> bool:
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
            logger.info("Счёт %s подтверждён", payment_id)
        return ok

    async def create_yookassa_payment(
        self,
        student_id: str,
        student_name: str,
        period_month: str,
        total_amount: int,
    ) -> str:
        """Создаёт платёж в ЮКасса, возвращает confirmation_url для клиента."""
        from yookassa import Configuration, Payment as YKPayment
        from config.settings import settings
        Configuration.configure(settings.yookassa_shop_id, settings.yookassa_secret_key)
        idempotency_key = str(uuid.uuid4())
        payment = YKPayment.create({
            "amount": {"value": f"{total_amount}.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": settings.yookassa_return_url,
            },
            "capture": True,
            "description": f"{student_name} — {period_month}",
            "metadata": {
                "student_id": student_id,
                "period_month": period_month,
            },
        }, idempotency_key)
        return payment.confirmation.confirmation_url

    async def confirm_period(
        self,
        student_id: str,
        period_month: str,
        confirmed_by_tg_id: int,
    ) -> int:
        """Подтверждает все счета периода. Возвращает кол-во подтверждённых."""
        count = await self._payment_repo.confirm_all_for_period(
            student_id, period_month, confirmed_by_tg_id,
        )
        logger.info("Период %s ученика %s оплачен (%d счётов)", period_month, student_id, count)
        return count

