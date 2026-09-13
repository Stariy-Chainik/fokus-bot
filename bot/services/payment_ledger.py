"""Накопительный счёт по педагогу за месяц: начислено − оплачено = к доплате.

Чистые функции без обращения к хранилищу. Оплаты по связке (ученик, педагог, месяц)
складываются; галочка «оплачен» на уроке ставится по порядку дат, пока хватает
оплаченной суммы (любой платёж закрывает самые ранние уроки).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TeacherLedger:
    teacher_id: str
    name: str
    accrued: int                      # начислено за месяц (сумма занятий / абонемент)
    paid: int                         # сумма подтверждённых оплат
    items: list = field(default_factory=list)       # Billing-строки занятий (для абонемента пусто)
    paid_rows: list = field(default_factory=list)   # StudentPeriodPayment со статусом paid
    pending: Optional[object] = None                # строка-остаток (status pending) или None
    subscription: bool = False

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


def lesson_paid_marks(amounts: list[int], paid: int) -> list[bool]:
    """amounts — суммы занятий в порядке дат; занятие оплачено, если хватает накопленной оплаты."""
    marks: list[bool] = []
    cumulative = 0
    for amount in amounts:
        cumulative += amount
        marks.append(cumulative <= paid)
    return marks


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


def lesson_marks(items: list, paid: int) -> list[dict]:
    """Занятия педагога за месяц с отметкой оплаты: [{lesson_id, date, duration_min, amount, paid}].

    Порядок — по дате; оплата накопительная (любой платёж закрывает самые ранние занятия).
    """
    ordered = sorted(items, key=lambda b: (b.date, b.lesson_id))
    marks = lesson_paid_marks([b.amount for b in ordered], paid)
    return [
        {"lesson_id": b.lesson_id, "date": b.date, "duration_min": b.duration_min,
         "amount": b.amount, "lesson_type": b.lesson_type, "paid": ok}
        for b, ok in zip(ordered, marks, strict=False)
    ]
