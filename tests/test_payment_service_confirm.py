"""PaymentService: подтверждения (одиночное / по педагогам / период), отметки занятий, платёж ЮКассы."""
from types import SimpleNamespace

from bot.models.enums import PaymentStatus
from bot.services import PaymentService
from config.settings import settings
from tests.fakes import PaymentRepoFake, mk_payment, mk_student, run


def _rows():
    return [
        mk_payment("PAY-1", "STU-1", "2026-09", "T1", 1000, teacher_name="Река"),
        mk_payment("PAY-2", "STU-1", "2026-09", "T2", 500, PaymentStatus.PAID, teacher_name="Власов", paid_at="2026-09-02"),
        mk_payment("PAY-3", "STU-1", "2026-09", "T3", 0, teacher_name="Никишин"),
        mk_payment("PAY-4", "STU-1", "2026-08", "T1", 700, teacher_name="Река"),
        mk_payment("PAY-5", "STU-2", "2026-09", "T1", 900, teacher_name="Река"),
    ]


def _svc(rows=None):
    repo = PaymentRepoFake(_rows() if rows is None else rows)
    return PaymentService(repo, lesson_repo=None, teacher_repo=None), repo


def test_confirm_payment_paths():
    svc, repo = _svc()
    assert run(svc.confirm_payment("PAY-1", 7, "receipt_bank")) is True
    assert run(svc.confirm_payment("PAY-2", 7)) is False      # уже оплачен
    assert run(svc.confirm_payment("PAY-3", 7)) is False      # нулевой остаток
    assert run(svc.confirm_payment("PAY-404", 7)) is False    # нет такого
    assert repo.confirmed == [("PAY-1", 7, "receipt_bank")]


def test_confirm_teachers_only_pending_positive_rows():
    svc, repo = _svc()
    count = run(svc.confirm_teachers("STU-1", "2026-09", ["T1", "T2", "T3", "T9"], 7, "cash"))
    assert count == 1
    assert repo.confirmed == [("PAY-1", 7, "cash")]


def test_confirm_period_confirms_all_pending_of_student_and_month():
    svc, repo = _svc()
    assert run(svc.confirm_period("STU-1", "2026-09", 7)) == 1
    assert [c[0] for c in repo.confirmed] == ["PAY-1"]
    assert {r.payment_id: r.status for r in repo.rows}["PAY-4"] == PaymentStatus.PENDING   # другой месяц не тронут
    assert {r.payment_id: r.status for r in repo.rows}["PAY-5"] == PaymentStatus.PENDING   # другой ученик не тронут


def _bill(lesson_id, date, amount, duration=45):
    return SimpleNamespace(lesson_id=lesson_id, date=date, amount=amount, duration_min=duration, lesson_type="individual")


def test_teacher_lesson_marks_and_missing_teacher():
    bills = {"T1": {"name": "Река", "total": 3000, "items": [_bill("L2", "2026-09-05", 1500), _bill("L1", "2026-09-01", 1500)]}}
    svc, repo = _svc([mk_payment("PAY-1", "STU-1", "2026-09", "T1", 1500, PaymentStatus.PAID)])

    async def _bills(sid, period):
        return bills
    svc.compute_bills_for_student_period = _bills
    marks, ledger = run(svc.teacher_lesson_marks(mk_student("STU-1"), "2026-09", "T1"))
    assert [(m["lesson_id"], m["paid"]) for m in marks] == [("L1", True), ("L2", False)]
    assert (ledger.accrued, ledger.paid, ledger.remainder) == (3000, 1500, 1500)
    assert run(svc.teacher_lesson_marks(mk_student("STU-1"), "2026-09", "T9")) == ([], None)


class _FakeYooKassa:
    def __init__(self):
        self.calls = []
        self.configured = None

    def configure(self, shop_id, secret):
        self.configured = (shop_id, secret)

    def create(self, payload, idempotency_key):
        self.calls.append((payload, idempotency_key))
        return SimpleNamespace(id="yk-1", confirmation=SimpleNamespace(confirmation_url="https://pay/1"))


def _patch_yookassa(monkeypatch):
    fake = _FakeYooKassa()
    monkeypatch.setattr("yookassa.Configuration.configure", fake.configure)
    monkeypatch.setattr("yookassa.Payment.create", fake.create)
    monkeypatch.setattr(settings, "yookassa_shop_id", "shop")
    monkeypatch.setattr(settings, "yookassa_secret_key", "secret")
    monkeypatch.setattr(settings, "yookassa_return_url", "https://t.me/fokus_bot")
    return fake


def test_create_yookassa_payment_card_with_school_email_receipt(monkeypatch):
    fake = _patch_yookassa(monkeypatch)
    monkeypatch.setattr(settings, "yookassa_receipt_email", "school@example.com")
    svc, _ = _svc([])
    url, pid = run(svc.create_yookassa_payment("STU-1", "Иванов Иван", "2026-09", 2500))
    assert (url, pid) == ("https://pay/1", "yk-1")
    assert fake.configured == ("shop", "secret")
    payload, key = fake.calls[0]
    assert len(key) == 36
    assert payload["amount"] == {"value": "2500.00", "currency": "RUB"}
    assert payload["confirmation"] == {"type": "redirect", "return_url": "https://t.me/fokus_bot"}
    assert payload["capture"] is True and payload["description"] == "Иванов Иван — 2026-09"
    assert payload["metadata"] == {"student_id": "STU-1", "period_month": "2026-09"}
    assert "payment_method_data" not in payload
    assert payload["receipt"] == {
        "customer": {"email": "school@example.com"},
        "items": [{
            "description": "Занятия — Иванов Иван, 2026-09", "quantity": "1.00",
            "amount": {"value": "2500.00", "currency": "RUB"}, "vat_code": 1,
            "payment_subject": "service", "payment_mode": "full_payment",
        }],
    }


def test_create_yookassa_payment_sbp_partial_and_client_email(monkeypatch):
    fake = _patch_yookassa(monkeypatch)
    monkeypatch.setattr(settings, "yookassa_receipt_email", "school@example.com")
    svc, _ = _svc([])
    run(svc.create_yookassa_payment("STU-1", "Иванов Иван", "2026-09", 800, sbp=True,
                                    customer_phone="+79990000000", customer_email="parent@example.com",
                                    teacher_ids=["T1", "T2"]))
    payload, _ = fake.calls[0]
    assert payload["payment_method_data"] == {"type": "sbp"}
    assert payload["receipt"]["customer"] == {"email": "parent@example.com"}   # телефон не используется
    assert payload["metadata"]["teacher_ids"] == "T1,T2"


def test_create_yookassa_payment_without_any_email_has_no_receipt(monkeypatch):
    fake = _patch_yookassa(monkeypatch)
    monkeypatch.setattr(settings, "yookassa_receipt_email", "")
    svc, _ = _svc([])
    run(svc.create_yookassa_payment("STU-1", "Иванов Иван", "2026-09", 800))
    assert "receipt" not in fake.calls[0][0]
