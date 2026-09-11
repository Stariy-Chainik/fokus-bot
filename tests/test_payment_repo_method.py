import asyncio

from bot.models import StudentPeriodPayment
from bot.models.enums import PaymentStatus
from bot.repositories.payment_repo import PaymentRepository, _row_to_payment


def _payment(method: str = "cash") -> StudentPeriodPayment:
    return StudentPeriodPayment(
        payment_id="PAY-000001", student_id="STU-1", student_name="Алиса",
        period_month="2026-09", total_amount=1300, status=PaymentStatus.PAID,
        paid_at="2026-09-11 12:00:00", confirmed_by_tg_id=7, comment=None,
        created_at="2026-09-11 12:00:00", updated_at="2026-09-11 12:00:00",
        teacher_id="T1", teacher_name="Мария", payment_method=method,
    )


class _Repo(PaymentRepository):
    def __init__(self, rows=None):
        self.rows = rows or []
        self.appended = []
        self.updated = []

    async def _all_records(self):
        return self.rows

    async def _append_row(self, values):
        self.appended.append(values)

    async def _update_cell(self, row, col, value):
        self.updated.append((row, col, value))

    def _invalidate_cache(self):
        pass


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
        "student_id": "STU-1", "period_month": "2026-09", "status": "pending",
        "total_amount": 1300,
    }])
    count = asyncio.run(repo.confirm_all_for_period("STU-1", "2026-09", 7, "receipt_sbp"))
    assert count == 1
    assert (2, 14, "receipt_sbp") in repo.updated
