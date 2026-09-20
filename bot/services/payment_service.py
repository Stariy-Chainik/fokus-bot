from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from bot.models import StudentPeriodPayment, Student, Teacher
from bot.models.enums import PaymentStatus, GroupBillingMode, LessonType
from bot.utils import generate_payment_id, now_str
from bot.utils.dates import last_periods
from bot.repositories import (
    PaymentRepository, LessonRepository, TeacherRepository,
)
from .billing_service import build_billing_rows
from .payment_ledger import (
    BillAggregate, StudentMonthLessons, TeacherLedger, lesson_marks, mark_student_lessons, paid_lesson_ids,
)
from .payment_methods import ADMIN_MANUAL, YOOKASSA

logger = logging.getLogger(__name__)

# Ключ «педагога» для абонементного начисления в счетах/долгах: SUB:{group_id}.
# Абонемент — продукт группы, а не педагога, но модель инвойса требует teacher_id;
# синтетический ключ хранится в той же строковой колонке.
SUBSCRIPTION_KEY_PREFIX = "SUB:"


@dataclass(frozen=True)
class SubscriptionMemberRow:
    """Участник абонементной группы за месяц: начислено (0 — освобождён или вне членства) и оплачено."""
    student_id: str
    accrued: int
    paid: int
    active: bool  # членство покрывает этот месяц
_SUMMER_MONTHS = (7, 8)


def subscription_billable_months(lesson_months: set, until: str) -> set:
    """Месяцы, за которые начисляется абонемент группы (правило 2026-09-08).

    Абонемент платится каждый месяц с первого занятия группы по `until`
    включительно — каникулы тоже, — КРОМЕ июля и августа: летом начисляем
    только за месяц, в котором у группы реально были занятия.
    """
    if not lesson_months:
        return set()
    year, month = (int(x) for x in min(lesson_months).split("-"))
    until_year, until_month = (int(x) for x in until.split("-"))
    out: set = set()
    while (year, month) <= (until_year, until_month):
        period = f"{year:04d}-{month:02d}"
        if month not in _SUMMER_MONTHS or period in lesson_months:
            out.add(period)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


@dataclass
class DebtorRow:
    """Строка экрана «Должники»: долг ученика по месяцам (см. PaymentService.compute_debt_map)."""
    student: Student
    periods: dict[str, int]   # период → ₽, по возрастанию
    total: int
    closed_total: int         # только закрытые (прошедшие) месяцы — идёт в напоминание
    has_parent: bool          # у родителя есть адрес в Telegram или MAX


def build_debtor_rows(
    debt_map: dict[str, dict[str, int]], students_by_id: dict[str, Student], current_period: str,
) -> list[DebtorRow]:
    """Карта долгов → строки по убыванию долга; ученики, которых нет в справочнике, пропускаются."""
    rows: list[DebtorRow] = []
    for sid, periods in debt_map.items():
        student = students_by_id.get(sid)
        if student is None:
            logger.warning("Должник %s не найден в students — пропускаем", sid)
            continue
        rows.append(DebtorRow(
            student=student,
            periods=dict(sorted(periods.items())),
            total=sum(periods.values()),
            closed_total=sum(amt for p, amt in periods.items() if p < current_period),
            has_parent=bool(student.parent_addrs),
        ))
    rows.sort(key=lambda d: d.total, reverse=True)
    return rows


class PaymentService:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        lesson_repo: LessonRepository,
        teacher_repo: TeacherRepository,
        group_repo=None,
        student_group_repo=None,
        subscription_override_repo=None,
    ) -> None:
        self._payment_repo = payment_repo
        self._lesson_repo = lesson_repo
        self._teacher_repo = teacher_repo
        # Опциональны (для абонементных начислений); без них подписки не считаются.
        self._group_repo = group_repo
        self._student_group_repo = student_group_repo
        self._sub_override_repo = subscription_override_repo
        self._sub_since: dict[tuple[str, str], str] = {}   # постоянное правило действует с этого месяца

    async def _sub_override_map(self) -> dict[tuple[str, str, str], int]:
        """(group_id, period|'*', student_id|'') → amount.

        ``period='*'`` — постоянное персональное правило; пустой student_id — вся группа.
        У постоянного правила запоминается ещё и месяц создания (`_sub_since`):
        оно действует с этого месяца и вперёд, прошлые месяцы не переписывает.
        """
        if self._sub_override_repo is None:
            return {}
        rows = await self._sub_override_repo.get_all()
        self._sub_since = {
            (o.group_id, o.student_id or ""): (o.created_at or "")[:7]
            for o in rows if o.period_month == "*"
        }
        return {(o.group_id, o.period_month, o.student_id or ""): o.amount for o in rows}

    async def subscription_revenue_breakdown(
        self, period_month: str,
    ) -> list[tuple[str, int, int]]:
        """Абонементная выручка периода по группам: [(имя группы, учеников, сумма ₽)]."""
        return [(name, billed, total) for _gid, name, billed, total in await self.subscription_revenue_rows(period_month)]

    async def subscription_revenue_rows(
        self, period_month: str,
    ) -> list[tuple[str, str, int, int]]:
        """Абонементная выручка периода по группам: [(group_id, имя группы, учеников, сумма ₽)].

        Та же логика, что в счетах/долгах: группа активна (≥1 занятие в месяце),
        каждому участнику — цена месяца (override ученика → группы → price_full),
        0 = освобождён (не считается). Сортировка по имени группы.
        """
        if self._group_repo is None or self._student_group_repo is None:
            return []
        sub_groups = [
            g for g in await self._group_repo.get_all(include_archived=True)
            if g.billing_mode == GroupBillingMode.SUBSCRIPTION
        ]
        if not sub_groups:
            return []
        active: set[str] = set()
        sub_ids = {g.group_id for g in sub_groups}
        for ls in await self._lesson_repo.get_all():
            if (ls.type == LessonType.GROUP and ls.group_id in sub_ids
                    and ls.date[:7] == period_month):
                active.add(ls.group_id)
        if not active:
            return []
        overrides = await self._sub_override_map()
        membership = await self._student_group_repo.get_membership_map()
        result: list[tuple[str, str, int, int]] = []
        for g in sorted(sub_groups, key=lambda x: x.name):
            if g.group_id not in active:
                continue
            members = [sid for (sid, gid) in membership if gid == g.group_id]  # вместе с ушедшими
            billed = 0
            total = 0
            for sid in members:
                row = membership.get((sid, g.group_id))
                if row is not None and not row.covers(period_month):
                    continue  # вне периода членства
                amount = self._sub_amount(overrides, g.group_id, period_month, sid, g.price_full)
                if amount > 0:
                    billed += 1
                    total += amount
            if total > 0:
                result.append((g.group_id, g.name, billed, total))
        return result

    async def subscription_group_detail(
        self, group_id: str, period_month: str,
    ) -> list[SubscriptionMemberRow] | None:
        """Состав абонементной группы за месяц: кому сколько начислено и кто сколько оплатил.

        None — группы нет или она не абонементная. Начисление — как в счетах: только за
        месяц с занятием группы (`subscription_billable_months`), за месяцы членства,
        с учётом переопределений (0 — освобождён). Оплачено — сумма PAID-строк по ключу
        SUB:{group_id}; ушедший, но оплативший, тоже показывается (active=False).
        """
        if self._group_repo is None or self._student_group_repo is None:
            return None
        group = await self._group_repo.get_by_id(group_id)
        if group is None or group.billing_mode != GroupBillingMode.SUBSCRIPTION:
            return None
        months = {ls.date[:7] for ls in await self._lesson_repo.get_all()
                  if ls.type == LessonType.GROUP and ls.group_id == group_id}
        billable = period_month in subscription_billable_months(months, until=period_month)
        overrides = await self._sub_override_map()
        membership = await self._student_group_repo.get_membership_map()
        key = f"{SUBSCRIPTION_KEY_PREFIX}{group_id}"
        paid: dict[str, int] = {}
        for pay in await self._payment_repo.get_all():
            if pay.teacher_id == key and pay.period_month == period_month and pay.status == PaymentStatus.PAID:
                paid[pay.student_id] = paid.get(pay.student_id, 0) + pay.total_amount
        rows: list[SubscriptionMemberRow] = []
        for (sid, gid), row in membership.items():
            if gid != group_id:
                continue
            active = row.covers(period_month)
            amount = self._sub_amount(overrides, group_id, period_month, sid, group.price_full) if active and billable else 0
            rows.append(SubscriptionMemberRow(sid, max(amount, 0), paid.pop(sid, 0), active))
        rows.extend(SubscriptionMemberRow(sid, 0, amount, False) for sid, amount in paid.items())
        return rows

    async def pin_subscription_history(
        self, group_id: str, effective_period: str, pin_amount: int,
    ) -> int:
        """Зафиксировать прошлые месяцы абонемента перед сменой цены («только вперёд»).

        Для каждого месяца строго раньше effective_period, где у группы было хотя бы
        одно занятие и нет group-wide переопределения, создаётся переопределение
        pin_amount (старая цена при смене; 0 при первом включении абонемента).
        Возвращает число зафиксированных месяцев.
        """
        if self._sub_override_repo is None:
            return 0
        lesson_months = {
            ls.date[:7]
            for ls in await self._lesson_repo.get_all()
            if ls.type == LessonType.GROUP and ls.group_id == group_id
        }
        months = {
            m for m in subscription_billable_months(lesson_months, until=effective_period)
            if m < effective_period
        }
        if not months:
            return 0
        existing = {
            o.period_month
            for o in await self._sub_override_repo.get_for_group(group_id)
            if o.student_id is None
        }
        pinned = 0
        for month in sorted(months - existing):
            await self._sub_override_repo.upsert(group_id, month, None, pin_amount)
            pinned += 1
        if pinned:
            logger.info("Абонемент %s: зафиксировано %d прошлых мес. по %d ₽ (новая цена с %s)",
                        group_id, pinned, pin_amount, effective_period)
        return pinned

    def _sub_amount(
        self,
        overrides: dict[tuple[str, str, str], int],
        group_id: str, period: str, student_id: str, default: int,
    ) -> int:
        """Цена абонемента: ученик/месяц → постоянное правило ученика → группа/месяц → price_full.

        Постоянное правило действует с месяца, когда его завели, и вперёд: закрытые
        месяцы оно не переписывает (решение 2026-09-20).
        """
        key_student = (group_id, period, student_id)
        if key_student in overrides:
            return overrides[key_student]
        key_student_permanent = (group_id, "*", student_id)
        if key_student_permanent in overrides:
            since = getattr(self, "_sub_since", {}).get((group_id, student_id), "")
            if not since or period >= since:
                return overrides[key_student_permanent]
        key_group = (group_id, period, "")
        if key_group in overrides:
            return overrides[key_group]
        return default

    async def _subscription_bills_for_student(
        self, student_id: str, period_month: str,
    ) -> dict[str, BillAggregate]:
        """Абонементные начисления ученика за период: {SUB:gid → BillAggregate}.

        Правило: группа с billing_mode=SUBSCRIPTION → фиксированная price_full
        ₽/месяц с ученика, НЕЗАВИСИМО от числа занятий; начисляется, только если
        в этом месяце у группы было хотя бы одно занятие (каникулы — не платят).
        """
        if self._group_repo is None or self._student_group_repo is None:
            return {}
        gids = await self._student_group_repo.get_groups_for_student(student_id, include_left=True)
        if not gids:
            return {}
        groups_by_id = {g.group_id: g for g in await self._group_repo.get_all(include_archived=True)}
        sub_groups = []
        for gid in gids:
            group = groups_by_id.get(gid)
            if group and group.billing_mode == GroupBillingMode.SUBSCRIPTION:
                sub_groups.append(group)
        if not sub_groups:
            return {}
        # Месяцы занятий каждой группы — одним проходом по занятиям.
        sub_ids = {g.group_id for g in sub_groups}
        lesson_months: dict[str, set[str]] = {}
        for ls in await self._lesson_repo.get_all():
            if ls.type == LessonType.GROUP and ls.group_id in sub_ids:
                lesson_months.setdefault(ls.group_id, set()).add(ls.date[:7])
        overrides = await self._sub_override_map()
        membership = await self._student_group_repo.get_membership_map()
        result: dict[str, BillAggregate] = {}
        for group in sub_groups:
            billable = subscription_billable_months(
                lesson_months.get(group.group_id, set()), until=period_month,
            )
            if period_month not in billable:
                continue
            # Начисляем только за месяцы, когда ученик числился в группе
            row = membership.get((student_id, group.group_id))
            if row is not None and not row.covers(period_month):
                continue
            amount = self._sub_amount(
                overrides, group.group_id, period_month, student_id, group.price_full,
            )
            if amount <= 0:  # 0 = освобождение в этом месяце
                continue
            result[f"{SUBSCRIPTION_KEY_PREFIX}{group.group_id}"] = BillAggregate(
                name="Абонемент",  # без названия группы — не влезает в счёт
                total=amount, subscription=True,
            )
        return result

    async def compute_bills_for_student_period(
        self, student_id: str, period_month: str,
    ) -> dict[str, BillAggregate]:
        """
        On-demand расчёт счёта ученика за период.
        Возвращает dict[teacher_id | SUB:gid] -> BillAggregate (имя, сумма, Billing-строки).
        """
        lessons = await self._lesson_repo.get_by_student_and_period(student_id, period_month)
        teachers_cache: dict[str, Teacher] = {}
        result: dict[str, BillAggregate] = {}
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
                agg = result.setdefault(b.teacher_id, BillAggregate(name=b.teacher_name))
                agg.total += b.amount
                agg.items.append(b)
        await self._label_group_bills(result)
        result.update(await self._subscription_bills_for_student(student_id, period_month))
        return result

    async def _label_group_bills(self, bills: dict[str, BillAggregate]) -> None:
        """Строка счёта, где все занятия групповые, подписывается названием группы, а не педагога.

        Ключ остаётся teacher_id (учёт оплат по педагогу не меняется); при нескольких группах
        одного педагога названия перечисляются через запятую.
        """
        if self._group_repo is None:
            return
        group_names: dict[str, str] | None = None
        for agg in bills.values():
            if not agg.items or not all(b.lesson_type == "group" and b.group_id for b in agg.items):
                continue
            if group_names is None:
                group_names = {g.group_id: g.name for g in await self._group_repo.get_all(include_archived=True)}
            names = list(dict.fromkeys(group_names.get(b.group_id, b.group_id) for b in agg.items))
            agg.name = ", ".join(names)
            agg.group = True

    async def ledger_for(self, student: Student, period_month: str) -> dict:
        """Накопительный счёт по педагогам за месяц: teacher_id → TeacherLedger.

        Синхронизирует строку-остаток (pending) в листе: остаток = начислено − оплачено.
        Оплаченные строки не трогаются — их может быть несколько (оплата после каждого урока).
        """
        bills = await self.compute_bills_for_student_period(student.student_id, period_month)
        if not bills:
            return {}
        rows = await self._payment_repo.get_by_student_and_period(student.student_id, period_month)
        by_teacher: dict[str, list] = {}
        for r in rows:
            by_teacher.setdefault(r.teacher_id, []).append(r)
        ledgers: dict[str, TeacherLedger] = {}
        for teacher_id, agg in bills.items():
            t_rows = by_teacher.get(teacher_id, [])
            paid_rows = [r for r in t_rows if r.status == PaymentStatus.PAID]
            pending = next((r for r in t_rows if r.status != PaymentStatus.PAID), None)
            paid = sum(r.total_amount for r in paid_rows)
            remainder = max(agg.total - paid, 0)
            if pending is None:
                if remainder > 0:
                    pending = await self._create_invoice(student, period_month, teacher_id, agg.name, remainder)
            elif pending.total_amount != remainder:
                logger.info("Остаток по %s изменился: %d → %d", pending.payment_id, pending.total_amount, remainder)
                await self._payment_repo.update_amount(pending.payment_id, remainder)
                pending.total_amount = remainder
            ledgers[teacher_id] = TeacherLedger(
                teacher_id=teacher_id, name=agg.name, accrued=agg.total, paid=paid,
                items=list(agg.items), paid_rows=paid_rows, pending=pending,
                subscription=agg.subscription, group=agg.group,
            )
        return ledgers

    async def _create_invoice(
        self, student: Student, period_month: str, teacher_id: str, teacher_name: str, amount: int,
    ) -> StudentPeriodPayment:
        now = now_str()
        payment = StudentPeriodPayment(
            payment_id=generate_payment_id(await self._payment_repo.get_existing_ids()),
            student_id=student.student_id, student_name=student.name, period_month=period_month,
            total_amount=amount, status=PaymentStatus.PENDING, paid_at=None,
            confirmed_by_tg_id=None, comment=None, created_at=now, updated_at=now,
            teacher_id=teacher_id, teacher_name=teacher_name,
        )
        await self._payment_repo.add(payment)
        logger.info("Создан счёт %s student=%s teacher=%s период=%s сумма=%d",
                    payment.payment_id, student.student_id, teacher_id, period_month, amount)
        return payment

    async def student_lesson_marks(self, student_id: str, period_month: str) -> StudentMonthLessons:
        """Занятия ученика за месяц с долей ученика и отметкой оплаты — экран «Занятия» родителя."""
        lessons = await self._lesson_repo.get_by_student_and_period(student_id, period_month)
        teachers = {t.teacher_id: t for t in await self._teacher_repo.get_all()}
        rows = await self._payment_repo.get_by_student_and_period(student_id, period_month)
        return mark_student_lessons(student_id, lessons, teachers, rows)

    async def teacher_lesson_marks(
        self, student, period_month: str, teacher_id: str,
    ) -> tuple[list[dict], TeacherLedger | None]:
        """(занятия педагога с отметками оплаты, TeacherLedger) — для экрана выбора занятий."""
        ledgers = await self.ledger_for(student, period_month)
        ledger = ledgers.get(teacher_id)
        if ledger is None:
            return [], None
        rows = await self._payment_repo.get_by_student_and_period(student.student_id, period_month)
        return lesson_marks(ledger.items, ledger.paid, paid_lesson_ids(rows).get(teacher_id, set())), ledger

    async def set_payment_intent(
        self, student, period_month: str, teacher_id: str, lesson_ids: list,
    ) -> bool:
        """Запомнить на строке-остатке, за какие занятия платит родитель.

        Любое подтверждение (ЮКасса, чек, наличные) зачтёт оплату именно на них —
        `record_payment` читает намерение со строки-остатка.
        """
        ledger = (await self.ledger_for(student, period_month)).get(teacher_id)
        if ledger is None or ledger.pending is None:
            return False
        return await self._payment_repo.set_lesson_ids(ledger.pending.payment_id, "|".join(lesson_ids))

    async def record_payment(
        self, student_id: str, student_name: str, period_month: str, amount: int,
        confirmed_by_tg_id: int, teacher_ids: list | None = None, comment: str | None = None,
        payment_method: str = "", lesson_ids: list | None = None,
    ) -> tuple[int, int]:
        """Зачесть оплату на сумму amount по остаткам педагогов месяца.

        teacher_ids — какие остатки закрывать и в каком порядке (None — все, по имени).
        lesson_ids — занятия, за которые платят (плательщик выбрал их на экране): они
        запоминаются в строке оплаты и получают ✅ именно они, а не самые ранние.
        Учитываются, только когда оплата адресована одному начислению.
        Остаток закрывается целиком (строка → paid) или частично (новая paid-строка на
        зачтённую сумму, остаток уменьшается). Лишнее — переплата отдельной строкой.
        Возвращает (зачтено ₽, строк). Так сумма в чеке/платеже совпадает с учётом,
        даже если остаток вырос после новых занятий.
        """
        linked = "|".join(lesson_ids) if lesson_ids and teacher_ids and len(teacher_ids) == 1 else ""
        if not payment_method:
            payment_method = YOOKASSA if confirmed_by_tg_id == 0 else ADMIN_MANUAL
        student = Student(student_id=student_id, name=student_name)
        ledgers = await self.ledger_for(student, period_month)
        if teacher_ids:
            order = [tid for tid in teacher_ids if tid in ledgers]
        else:
            order = sorted(ledgers, key=lambda t: ledgers[t].name)
        left, credited, rows = int(amount), 0, 0
        for tid in order:
            if left <= 0:
                break
            pending = ledgers[tid].pending
            if pending is None or pending.total_amount <= 0:
                continue
            pay = min(left, pending.total_amount)
            # Занятия оплаты: явно переданные с экрана или намерение, которое
            # оставил плательщик на строке-остатке (родитель выбрал занятия).
            row_ids = linked or (getattr(pending, "lesson_ids", "") or "")
            if pay == pending.total_amount:
                await self._payment_repo.confirm(
                    pending.payment_id, confirmed_by_tg_id, payment_method,
                )
                if row_ids:
                    await self._payment_repo.set_lesson_ids(pending.payment_id, row_ids)
            else:
                await self._add_paid_row(
                    student, period_month, tid, ledgers[tid].name, pay,
                    confirmed_by_tg_id, comment, payment_method, row_ids,
                )
                await self._payment_repo.update_amount(pending.payment_id, pending.total_amount - pay)
                if not linked and row_ids:  # намерение израсходовано
                    await self._payment_repo.set_lesson_ids(pending.payment_id, "")
            left -= pay
            credited += pay
            rows += 1
        if left > 0 and order:  # переплата — фиксируем на первого педагога из списка
            tid = order[0]
            await self._add_paid_row(
                student, period_month, tid, ledgers[tid].name, left,
                confirmed_by_tg_id, "переплата", payment_method,
            )
            credited += left
            rows += 1
        logger.info("Оплата зачтена: student=%s period=%s сумма=%d строк=%d", student_id, period_month, credited, rows)
        return credited, rows

    async def _add_paid_row(
        self, student: Student, period_month: str, teacher_id: str, teacher_name: str,
        amount: int, confirmed_by_tg_id: int, comment: str | None, payment_method: str,
        lesson_ids: str = "",
    ) -> StudentPeriodPayment:
        now = now_str()
        payment = StudentPeriodPayment(
            payment_id=generate_payment_id(await self._payment_repo.get_existing_ids()),
            student_id=student.student_id, student_name=student.name, period_month=period_month,
            total_amount=amount, status=PaymentStatus.PAID, paid_at=now,
            confirmed_by_tg_id=confirmed_by_tg_id, comment=comment, created_at=now, updated_at=now,
            teacher_id=teacher_id, teacher_name=teacher_name, payment_method=payment_method,
            lesson_ids=lesson_ids,
        )
        await self._payment_repo.add(payment)
        return payment

    async def get_or_create_invoices_for_student_period(
        self, student: Student, period_month: str,
    ) -> list[StudentPeriodPayment]:
        """Все строки счетов ученика за месяц (оплаты + остатки) после синхронизации остатков.
        Совместимость: раньше — по одной строке на педагога; теперь у педагога может быть
        несколько оплаченных строк и одна строка-остаток."""
        ledgers = await self.ledger_for(student, period_month)
        rows: list[StudentPeriodPayment] = []
        for ledger in ledgers.values():
            rows.extend(ledger.paid_rows)
            if ledger.pending is not None:
                rows.append(ledger.pending)
        return rows

    async def compute_debt_map(
        self, since_period: str | None = None, until_period: str | None = None,
    ) -> dict[str, dict[str, int]]:
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

        # Абонементные начисления: цена месяца (учитывая переопределения ученик → группа →
        # price_full) каждому участнику SUBSCRIPTION-группы за каждый месяц с ≥1 занятием.
        if self._group_repo is not None and self._student_group_repo is not None:
            sub_groups = [
                g for g in await self._group_repo.get_all(include_archived=True)
                if g.billing_mode == GroupBillingMode.SUBSCRIPTION
            ]
            if sub_groups:
                overrides = await self._sub_override_map()
                months_by_group: dict[str, set[str]] = {}
                for ls in lessons:
                    if ls.type == LessonType.GROUP and ls.group_id:
                        months_by_group.setdefault(ls.group_id, set()).add(ls.date[:7])
                until = until_period or last_periods(1)[0]
                membership = await self._student_group_repo.get_membership_map()
                for g in sub_groups:
                    members = [sid for (sid, gid) in membership if gid == g.group_id]  # вместе с ушедшими
                    billable = subscription_billable_months(
                        months_by_group.get(g.group_id, set()), until=until,
                    )
                    for period in sorted(billable):
                        for sid in members:
                            row = membership.get((sid, g.group_id))
                            if row is not None and not row.covers(period):
                                continue  # вне периода членства
                            amount = self._sub_amount(
                                overrides, g.group_id, period, sid, g.price_full,
                            )
                            if amount <= 0:
                                continue
                            key = (sid, f"{SUBSCRIPTION_KEY_PREFIX}{g.group_id}", period)
                            accrued[key] = accrued.get(key, 0) + amount

        paid: dict[tuple[str, str, str], int] = {}
        for p in await self._payment_repo.get_all():
            if p.status == PaymentStatus.PAID:
                key = (p.student_id, p.teacher_id, p.period_month)
                paid[key] = paid.get(key, 0) + p.total_amount

        debts: dict[str, dict[str, int]] = {}
        for (sid, tid, period), amount in accrued.items():
            if since_period and period < since_period:
                continue
            debt = amount - paid.get((sid, tid, period), 0)  # накопительно: доплата после новых уроков
            if debt <= 0:
                continue
            per_student = debts.setdefault(sid, {})
            per_student[period] = per_student.get(period, 0) + debt
        return debts

    async def confirm_payment(
        self, payment_id: str, confirmed_by_tg_id: int,
        payment_method: str = ADMIN_MANUAL,
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
        if payment.total_amount <= 0:
            logger.warning("Счёт %s с нулевым остатком — подтверждать нечего", payment_id)
            return False
        ok = await self._payment_repo.confirm(payment_id, confirmed_by_tg_id, payment_method)
        if ok:
            logger.info("Счёт %s подтверждён", payment_id)
        return ok

    async def create_yookassa_payment(
        self,
        student_id: str,
        student_name: str,
        period_month: str,
        total_amount: int,
        sbp: bool = False,
        customer_phone: str = "",
        customer_email: str = "",
        teacher_ids: list | None = None,
        lesson_ids: list | None = None,
    ) -> tuple:
        """Создаёт платёж в ЮКасса, возвращает (confirmation_url, payment_id).

        sbp=True — сразу метод СБП (без выбора на странице ЮКассы).
        Магазин с фискализацией требует чек: контакт берём из телефона клиента,
        иначе — YOOKASSA_RECEIPT_EMAIL.
        """
        from yookassa import Configuration, Payment as YKPayment
        from config.settings import settings
        Configuration.configure(settings.yookassa_shop_id, settings.yookassa_secret_key)
        idempotency_key = str(uuid.uuid4())
        extra: dict[str, Any] = {"payment_method_data": {"type": "sbp"}} if sbp else {}
        # Чек — только на почту: email клиента, иначе служебный email школы.
        # Телефон не используем (СМС от ОФД платные) — решение 2026-09-08.
        customer = {}
        if customer_email:
            customer = {"email": customer_email}
        elif settings.yookassa_receipt_email:
            customer = {"email": settings.yookassa_receipt_email}
        if customer:
            extra["receipt"] = {
                "customer": customer,
                "items": [{
                    "description": f"Занятия — {student_name}, {period_month}"[:128],
                    "quantity": "1.00",
                    "amount": {"value": f"{total_amount}.00", "currency": "RUB"},
                    "vat_code": 1,  # без НДС
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }],
            }
        payment = YKPayment.create({
            **extra,
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
                # выборочная оплата: подтверждаем только этих педагогов
                **({"teacher_ids": ",".join(teacher_ids)} if teacher_ids else {}),
                # и только эти занятия, если родитель выбрал их на экране
                **({"lesson_ids": "|".join(lesson_ids)} if lesson_ids else {}),
            },
        }, idempotency_key)
        return payment.confirmation.confirmation_url, payment.id

    async def confirm_teachers(
        self,
        student_id: str,
        period_month: str,
        teacher_ids: list,
        confirmed_by_tg_id: int,
        payment_method: str = ADMIN_MANUAL,
    ) -> int:
        """Подтверждает счета периода только по выбранным педагогам."""
        count = 0
        for tid in teacher_ids:
            row = await self._payment_repo.get_by_student_period_teacher(
                student_id, period_month, tid,
            )
            if row and row.status != PaymentStatus.PAID and row.total_amount > 0:
                if await self._payment_repo.confirm(
                    row.payment_id, confirmed_by_tg_id, payment_method,
                ):
                    count += 1
        logger.info(
            "Частичная оплата: student=%s period=%s педагоги=%s подтверждено=%d",
            student_id, period_month, ",".join(teacher_ids), count,
        )
        return count

    async def confirm_period(
        self,
        student_id: str,
        period_month: str,
        confirmed_by_tg_id: int,
        payment_method: str = ADMIN_MANUAL,
    ) -> int:
        """Подтверждает все счета периода. Возвращает кол-во подтверждённых."""
        count = await self._payment_repo.confirm_all_for_period(
            student_id, period_month, confirmed_by_tg_id, payment_method,
        )
        logger.info("Период %s ученика %s оплачен (%d счётов)", period_month, student_id, count)
        return count
