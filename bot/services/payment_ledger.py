"""Накопительный счёт по педагогу за месяц: начислено − оплачено = к доплате.

Чистые функции без обращения к хранилищу. Оплаты по связке (ученик, педагог, месяц)
складываются; галочка «оплачен» на уроке ставится по порядку дат, пока хватает
оплаченной суммы (любой платёж закрывает самые ранние уроки).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot.models import StudentPeriodPayment


@dataclass
class BillAggregate:
    """Начисление ученику за месяц по одному ключу: педагог (занятия) или абонемент группы."""
    name: str
    total: int = 0
    items: list = field(default_factory=list)   # Billing-строки занятий; у абонемента пусто
    subscription: bool = False
    group: bool = False  # подпись — название группы (все занятия групповые), а не педагог


@dataclass
class TeacherLedger:
    teacher_id: str
    name: str
    accrued: int                      # начислено за месяц (сумма занятий / абонемент)
    paid: int                         # сумма подтверждённых оплат
    items: list = field(default_factory=list)       # Billing-строки занятий (для абонемента пусто)
    paid_rows: list = field(default_factory=list)   # StudentPeriodPayment со статусом paid
    pending: Optional[StudentPeriodPayment] = None  # строка-остаток (status pending) или None
    subscription: bool = False
    group: bool = False               # name — название группы, а не педагога

    @property
    def remainder(self) -> int:
        return max(self.accrued - self.paid, 0)

    @property
    def overpaid(self) -> int:
        return max(self.paid - self.accrued, 0)

    @property
    def fully_paid(self) -> bool:
        return self.accrued > 0 and self.remainder == 0

    @property
    def pending_pid(self) -> int:
        """Числовая часть PAY-id строки-остатка (для коротких callback), 0 если нет."""
        if self.pending is None:
            return 0
        try:
            return int(self.pending.payment_id.split("-")[-1])
        except (ValueError, AttributeError):
            return 0


def lesson_paid_marks(amounts: list[int], paid: int, linked: list[bool] | None = None) -> list[bool]:
    """Отметки оплаты занятий в порядке дат.

    linked — занятия, прямо указанные в оплате (плательщик выбрал именно их): они
    оплачены всегда, их суммы вычитаются из оплаты. Остальные закрываются накопительно
    с самых ранних — как было до появления выбора занятий.
    """
    linked = linked or [False] * len(amounts)
    left = paid - sum(a for a, ok in zip(amounts, linked, strict=False) if ok)
    marks: list[bool] = []
    cumulative = 0
    for amount, is_linked in zip(amounts, linked, strict=False):
        if is_linked:
            marks.append(True)
            continue
        cumulative += amount
        marks.append(cumulative <= left)
    return marks


def paid_lesson_ids(rows) -> dict[str, set[str]]:
    """teacher_id → занятия, прямо закрытые оплатами (колонка lesson_ids у paid-строк)."""
    out: dict[str, set[str]] = {}
    for p in rows:
        status = getattr(p.status, "value", p.status)
        ids = getattr(p, "lesson_id_list", None)
        if status == "paid" and ids:
            out.setdefault(p.teacher_id, set()).update(ids)
    return out


def paid_sums(rows) -> dict[str, int]:
    """teacher_id → сумма оплаченных строк (status paid)."""
    out: dict[str, int] = {}
    for p in rows:
        status = getattr(p.status, "value", p.status)
        if status == "paid":
            out[p.teacher_id] = out.get(p.teacher_id, 0) + int(p.total_amount)
    return out


def ledger_totals(ledgers: dict) -> tuple[int, int, int]:
    """(начислено, оплачено, к доплате) по всем педагогам."""
    accrued = sum(ledger.accrued for ledger in ledgers.values())
    paid = sum(ledger.paid for ledger in ledgers.values())
    remainder = sum(ledger.remainder for ledger in ledgers.values())
    return accrued, paid, remainder


def lesson_marks(items: list, paid: int, linked_ids: set | None = None) -> list[dict]:
    """Занятия педагога за месяц с отметкой оплаты: [{lesson_id, date, duration_min, amount, paid}].

    Порядок — по дате. Занятия из linked_ids (их выбрал плательщик) отмечены явно,
    остальные закрываются накопительно с самых ранних.
    """
    ordered = sorted(items, key=lambda b: (b.date, b.lesson_id))
    linked = [b.lesson_id in (linked_ids or set()) for b in ordered]
    marks = lesson_paid_marks([b.amount for b in ordered], paid, linked)
    return [
        {"lesson_id": b.lesson_id, "date": b.date, "duration_min": b.duration_min,
         "amount": b.amount, "lesson_type": b.lesson_type, "paid": ok}
        for b, ok in zip(ordered, marks, strict=False)
    ]


@dataclass(frozen=True)
class LessonPaymentMark:
    amount: int   # доля ученика в занятии (0 — не тарифицируется: абонемент / NONE)
    paid: bool    # закрыто накопительными оплатами месяца по этому педагогу


@dataclass
class StudentMonthLessons:
    """Занятия ученика за месяц (по дате) и отметка оплаты каждого — экран «Занятия» родителя."""
    lessons: list
    marks: dict[str, LessonPaymentMark] = field(default_factory=dict)

    def mark(self, lesson_id: str) -> LessonPaymentMark:
        return self.marks.get(lesson_id, LessonPaymentMark(0, False))


def mark_student_lessons(
    student_id: str, month_lessons: list, teachers_by_id: dict, payment_rows: list,
) -> StudentMonthLessons:
    """Доля ученика в каждом занятии (build_billing_rows) и накопительная отметка оплаты.

    Оплаты месяца складываются по педагогу; занятия педагога идут по дате, галочка
    ставится, пока хватает оплаченной суммы (любой платёж закрывает самые ранние уроки).
    Занятие педагога, которого нет в справочнике, суммы не получает.
    """
    from bot.services.billing_service import build_billing_rows

    ordered = sorted(month_lessons, key=lambda ls: ls.date)
    paid_by = paid_sums(payment_rows)
    linked_by = paid_lesson_ids(payment_rows)
    amounts: dict[str, int] = {}
    for ls in ordered:
        teacher = teachers_by_id.get(ls.teacher_id)
        if teacher:
            amounts[ls.lesson_id] = sum(
                row.amount for row in build_billing_rows(ls, teacher) if row.student_id == student_id
            )
    by_teacher: dict[str, list] = {}
    for ls in ordered:
        if amounts.get(ls.lesson_id, 0) > 0:
            by_teacher.setdefault(ls.teacher_id, []).append(ls)
    paid_mark: dict[str, bool] = {}
    for teacher_id, group in by_teacher.items():
        chrono = sorted(group, key=lambda x: (x.date, x.lesson_id))
        linked = [x.lesson_id in linked_by.get(teacher_id, set()) for x in chrono]
        marks = lesson_paid_marks([amounts[x.lesson_id] for x in chrono], paid_by.get(teacher_id, 0), linked)
        for ls, ok in zip(chrono, marks, strict=False):
            paid_mark[ls.lesson_id] = ok
    return StudentMonthLessons(
        lessons=ordered,
        marks={ls.lesson_id: LessonPaymentMark(amounts.get(ls.lesson_id, 0), paid_mark.get(ls.lesson_id, False))
               for ls in ordered},
    )
