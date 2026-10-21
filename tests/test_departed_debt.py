"""Ушедший ученик (решение владельца 16.10.2026): его долг нигде не считается — как у удалённого."""
import asyncio

from bot.models.enums import PaymentStatus
from bot.services.payment_service import PaymentService, period_collection
from bot.utils.dates import current_period, next_period
from tests.fakes import StudentGroupRepoFake
from tests.test_debt_map import _FakeLessonRepo, _FakePaymentRepo, _FakeTeacherRepo, _lesson, _payment, _teacher

CUR = current_period()


def _svc(sg, payments=()):
    return PaymentService(
        payment_repo=_FakePaymentRepo(list(payments)),
        lesson_repo=_FakeLessonRepo([
            _lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A"),
            _lesson("LES-2", "TCH-0001", "2026-05-11", s1="STU-B"),
            _lesson("LES-3", "TCH-0001", "2026-05-12", s1="STU-C"),      # без групп — индивидуальные, долг остаётся
        ]),
        teacher_repo=_FakeTeacherRepo([_teacher()]),
        student_group_repo=sg,
    )


def test_departed_student_debt_is_dropped_everywhere():
    sg = StudentGroupRepoFake([
        ("STU-A", "GRP-0001", "2026-04", CUR),            # ушёл с этого месяца — долга нет
        ("STU-B", "GRP-0001", "2026-04", next_period()),  # уходит со следующего — этот месяц ещё в школе
    ])
    svc = _svc(sg)
    assert asyncio.run(svc._departed_students()) == {"STU-A"}
    debts = asyncio.run(svc.compute_debt_map())
    assert "STU-A" not in debts and debts["STU-B"] == {"2026-05": 1000} and debts["STU-C"] == {"2026-05": 1000}
    # вернулся — строка снова активна, долг снова виден
    sg.rows[0].left_period = ""
    assert asyncio.run(_svc(sg).compute_debt_map())["STU-A"] == {"2026-05": 1000}


def test_no_group_repo_means_nobody_departed():
    svc = PaymentService(payment_repo=_FakePaymentRepo([]), lesson_repo=_FakeLessonRepo([
        _lesson("LES-1", "TCH-0001", "2026-05-10", s1="STU-A")]), teacher_repo=_FakeTeacherRepo([_teacher()]))
    assert asyncio.run(svc.compute_debt_map()) == {"STU-A": {"2026-05": 1000}}


def test_departed_student_keeps_paid_part_in_collection():
    """Решение владельца 21.10.2026: у ушедшего убирается только неоплаченный остаток, оплаченное остаётся в сборе."""
    sg = StudentGroupRepoFake([("STU-A", "GRP-0001", "2026-04", CUR), ("STU-B", "GRP-0001", "2026-04", CUR)])
    svc = _svc(sg, [_payment("STU-A", "TCH-0001", "2026-05", PaymentStatus.PAID, amount=400)])   # частично оплатил
    ledger = asyncio.run(svc.compute_ledger_map())
    assert ledger[("STU-A", "TCH-0001", "2026-05")] == (400, 400)          # начислено урезано до оплаченного
    assert ("STU-B", "TCH-0001", "2026-05") not in ledger                   # ничего не платил — ключа нет
    col = period_collection(ledger, "2026-05")
    assert (col.accrued, col.paid) == (400 + 1000, 400)                    # STU-C (без групп) — 1000, долг виден
    assert "STU-A" not in asyncio.run(svc.compute_debt_map()) and "STU-B" not in asyncio.run(svc.compute_debt_map())
