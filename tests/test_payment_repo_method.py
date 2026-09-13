import asyncio

from bot.models import StudentPeriodPayment
from bot.models.enums import PaymentStatus
from bot.repositories.base import BaseRepository
from bot.repositories.payment_repo import PaymentRepository, _row_to_payment
from tests.fakes import FakeSheetsClient, FakeWorksheet

HEADERS = ["payment_id", "student_id", "student_name", "period_month", "total_amount", "status", "paid_at",
           "confirmed_by_tg_id", "comment", "created_at", "updated_at", "teacher_id", "teacher_name", "payment_method"]


def _payment(method: str = "cash") -> StudentPeriodPayment:
    return StudentPeriodPayment(
        payment_id="PAY-000001", student_id="STU-1", student_name="Алиса",
        period_month="2026-09", total_amount=1300, status=PaymentStatus.PAID,
        paid_at="2026-09-11 12:00:00", confirmed_by_tg_id=7, comment=None,
        created_at="2026-09-11 12:00:00", updated_at="2026-09-11 12:00:00",
        teacher_id="T1", teacher_name="Мария", payment_method=method,
    )


class _Repo(PaymentRepository):
    """PaymentRepository поверх листа в памяти; rows — словари по заголовкам HEADERS."""

    def __init__(self, rows=None):
        BaseRepository._cache.pop("student_payments", None)
        BaseRepository._headers.pop("student_payments", None)
        self.ws = FakeWorksheet(HEADERS, [[r.get(h) for h in HEADERS] for r in (rows or [])])
        super().__init__(FakeSheetsClient(self.ws), "student_payments")

    @property
    def appended(self):
        return [c[1] for c in self.ws.calls if c[0] == "append_row"]

    @property
    def updated(self):
        return [(c[1], c[2], c[3]) for c in self.ws.calls if c[0] == "update_cell"]


def test_row_parser_reads_method_and_legacy_blank():
    values = {
        "payment_id": "PAY-1", "student_id": "STU-1", "student_name": "Алиса",
        "period_month": "2026-09", "total_amount": 1300, "status": "paid",
        "paid_at": "2026-09-11", "confirmed_by_tg_id": 7, "comment": None,
        "created_at": "x", "updated_at": "x", "teacher_id": "T1", "teacher_name": "Мария",
    }
    assert _row_to_payment(values).payment_method == ""
    values["payment_method"] = "cash"
    assert _row_to_payment(values).payment_method == "cash"
    values["confirmed_by_tg_id"] = 0
    assert _row_to_payment(values).confirmed_by_tg_id == 0


def test_add_appends_method_as_column_14():
    repo = _Repo()
    asyncio.run(repo.add(_payment("receipt_bank")))
    assert len(repo.appended[0]) == 14
    assert repo.appended[0][13] == "receipt_bank"


def test_add_preserves_zero_as_automatic_confirmation_actor():
    repo = _Repo()
    payment = _payment("yookassa_sbp")
    payment.confirmed_by_tg_id = 0
    asyncio.run(repo.add(payment))
    assert repo.appended[0][7] == 0


def test_confirm_writes_method_to_column_14():
    repo = _Repo([{"payment_id": "PAY-000001"}])
    assert asyncio.run(repo.confirm("PAY-000001", 7, "cash"))
    assert (2, 14, "cash") in repo.updated


def test_confirm_period_writes_method_to_each_paid_row():
    repo = _Repo([{
        "payment_id": "PAY-000001", "student_id": "STU-1", "period_month": "2026-09", "status": "pending",
        "total_amount": 1300,
    }])
    count = asyncio.run(repo.confirm_all_for_period("STU-1", "2026-09", 7, "receipt_bank"))
    assert count == 1
    assert (2, 14, "receipt_bank") in repo.updated
