"""Тесты верификации webhook ЮКассы (fix B4 из docs/FOUND_BUGS.md).

Телу webhook доверять нельзя: process_yookassa_event обязан брать из него только
object.id и перепроверять платёж через API (здесь — фейковый fetch_payment).
"""
import asyncio
from types import SimpleNamespace

from bot.handlers.client.payments import process_yookassa_event


def _run(coro):
    return asyncio.run(coro)


class _FakePaymentService:
    def __init__(self):
        self.confirmed: list[tuple[str, str]] = []
        self.lesson_ids = []
        self.payment_methods: list[str] = []

    async def confirm_period(self, student_id, period_month, confirmed_by_tg_id):
        self.confirmed.append((student_id, period_month))
        return 1

    async def record_payment(
        self, student_id, student_name, period_month, amount, confirmed_by_tg_id,
        teacher_ids=None, comment=None, payment_method="", lesson_ids=None,
    ):
        self.payment_methods.append(payment_method)
        self.lesson_ids.append(lesson_ids)
        n = await self.confirm_period(student_id, period_month, confirmed_by_tg_id)
        return amount, n


class _FakeUserRepo:
    async def get_admins(self):
        return []  # уведомления в этих тестах не проверяем


def _api_payment(status="succeeded", student_id="STU-0001", period="2026-07", amount="500.00", method="sbp",
                 extra_meta=None):
    """Объект платежа, как его возвращает API ЮКассы (усечённо)."""
    return SimpleNamespace(
        status=status,
        metadata={"student_id": student_id, "period_month": period, **(extra_meta or {})},
        amount=SimpleNamespace(value=amount),
        payment_method=SimpleNamespace(type=method),
    )


def _event(payment_id="pay-123", meta=None):
    obj = {"id": payment_id}
    if meta is not None:
        obj["metadata"] = meta
    return {"event": "payment.succeeded", "object": obj}


def _fetch_returning(payment):
    async def fetch(payment_id):
        return payment
    return fetch


def test_succeeded_payment_confirms_period():
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    assert status == 200
    assert svc.confirmed == [("STU-0001", "2026-07")]
    assert svc.payment_methods == ["yookassa_sbp"]


def test_forged_body_metadata_is_ignored_api_is_source_of_truth():
    """Подделка: в теле metadata на другого ученика, но API говорит про STU-0001."""
    svc = _FakePaymentService()
    forged = _event(meta={"student_id": "STU-9999", "period_month": "2026-01"})
    _run(process_yookassa_event(
        forged, svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    # подтверждён тот (student, period), что вернул API, а не тело запроса
    assert svc.confirmed == [("STU-0001", "2026-07")]


def test_payment_not_found_in_api_does_not_confirm():
    """Подделка: платёж с таким id в нашем магазине не существует."""
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(meta={"student_id": "STU-0001", "period_month": "2026-07"}),
        svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(None),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_not_succeeded_status_does_not_confirm():
    svc = _FakePaymentService()
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment(status="pending")),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_api_error_returns_500_for_retry():
    """Сетевая ошибка проверки → 500, чтобы ЮКасса повторила уведомление."""
    svc = _FakePaymentService()

    async def failing_fetch(payment_id):
        raise ConnectionError("network down")

    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=failing_fetch,
    ))
    assert status == 500
    assert svc.confirmed == []


def test_missing_object_id_is_ignored():
    svc = _FakePaymentService()
    event = {"event": "payment.succeeded", "object": {"metadata": {"student_id": "STU-1", "period_month": "2026-07"}}}
    status = _run(process_yookassa_event(
        event, svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment()),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_other_events_are_ignored_without_api_call():
    svc = _FakePaymentService()
    calls = []

    async def counting_fetch(payment_id):
        calls.append(payment_id)
        return _api_payment()

    status = _run(process_yookassa_event(
        {"event": "payment.canceled", "object": {"id": "pay-123"}},
        svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=counting_fetch,
    ))
    assert status == 200
    assert calls == []
    assert svc.confirmed == []


def test_missing_metadata_in_api_payment_does_not_confirm():
    svc = _FakePaymentService()
    payment = SimpleNamespace(status="succeeded", metadata=None, amount=SimpleNamespace(value="500.00"))
    status = _run(process_yookassa_event(
        _event(), svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(payment),
    ))
    assert status == 200
    assert svc.confirmed == []


def test_selected_lessons_from_metadata_reach_the_ledger():
    """Родитель выбрал занятия: их id едут в metadata и зачитываются именно на них."""
    svc = _FakePaymentService()
    event = _event(meta={"student_id": "STU-0001", "period_month": "2026-09",
                         "teacher_ids": "TCH-0001", "lesson_ids": "LES-5|LES-7"})
    assert _run(process_yookassa_event(
        event, svc, bot=None, user_repo=_FakeUserRepo(),
        fetch_payment=_fetch_returning(_api_payment(
            extra_meta={"teacher_ids": "TCH-0001", "lesson_ids": "LES-5|LES-7"})),
    )) == 200
    assert svc.lesson_ids == [["LES-5", "LES-7"]]


def test_duplicate_webhook_does_not_double_credit_or_create_overpayment():
    """Повторный вебхук с тем же payment_id не создаёт дубль/переплату в реальном PaymentService."""
    from bot.models.enums import LessonType, PaymentStatus
    from bot.services.payment_service import PaymentService
    from tests.fakes import LessonRepoFake, PaymentRepoFake, TeacherRepoFake, mk_lesson, mk_teacher

    teacher = mk_teacher("TCH-0001", "Иванов Иван", rate_for_student=1000)
    lesson = mk_lesson("LES-0001", teacher, "2026-09-10", 45, LessonType.INDIVIDUAL,
                       students=[("STU-0001", "Петров Петя")])
    pay_repo = PaymentRepoFake()
    svc = PaymentService(pay_repo, LessonRepoFake([lesson]), TeacherRepoFake([teacher]))
    api_payment = _api_payment(student_id="STU-0001", period="2026-09", amount="1000.00")

    async def scenario():
        s1 = await process_yookassa_event(
            _event("pay-dup-1"), svc, bot=None, user_repo=_FakeUserRepo(),
            fetch_payment=_fetch_returning(api_payment),
        )
        s2 = await process_yookassa_event(
            _event("pay-dup-1"), svc, bot=None, user_repo=_FakeUserRepo(),
            fetch_payment=_fetch_returning(api_payment),
        )
        return s1, s2

    assert _run(scenario()) == (200, 200)
    paid_rows = [p for p in _run(pay_repo.get_all()) if p.status == PaymentStatus.PAID]
    assert len(paid_rows) == 1
    assert paid_rows[0].total_amount == 1000


def test_concurrent_webhook_and_watcher_credit_strictly_once():
    """Одновременный приход вебхука и срабатывание watcher на один payment_id зачитывают оплату ровно раз."""
    from bot.services.payment_watcher import _watch

    class _SlowPaymentService(_FakePaymentService):
        async def record_payment(
            self, student_id, student_name, period_month, amount, confirmed_by_tg_id,
            teacher_ids=None, comment=None, payment_method="", lesson_ids=None,
        ):
            await asyncio.sleep(0.05)
            return await super().record_payment(
                student_id, student_name, period_month, amount, confirmed_by_tg_id,
                teacher_ids, comment, payment_method, lesson_ids,
            )

    class _CountingBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.sent.append((chat_id, text))

    class _Admins:
        async def get_admins(self):
            return [SimpleNamespace(tg_id=777)]

    svc = _SlowPaymentService()
    bot = _CountingBot()
    api_payment = _api_payment(student_id="STU-0001", period="2026-09", amount="1000.00")

    async def scenario():
        await asyncio.gather(
            process_yookassa_event(
                _event("pay-race-1"), svc, bot=bot, user_repo=_Admins(),
                fetch_payment=_fetch_returning(api_payment),
            ),
            _watch(
                "pay-race-1", "STU-0001", "Петров Петя", "2026-09",
                svc, bot, _Admins(), parent_addr=None,
                interval=0, max_checks=2, fetch=_fetch_returning(api_payment),
            ),
        )

    _run(scenario())
    assert svc.confirmed == [("STU-0001", "2026-09")]
    assert len(bot.sent) == 1


def test_restart_dedup_via_activity_log_skips_recredit():
    """После перезапуска (новый PaymentService) повторный вебхук отсекается по маркеру yk:<id> в activity_log."""
    from bot.models.enums import LessonType, PaymentStatus
    from bot.services import activity
    from bot.services.payment_service import PaymentService
    from tests.fakes import ActivityRepoFake, LessonRepoFake, PaymentRepoFake, TeacherRepoFake, mk_lesson, mk_teacher

    feed = ActivityRepoFake()
    activity.setup(feed)
    try:
        teacher = mk_teacher("TCH-0001", "Иванов Иван", rate_for_student=1000)
        lesson = mk_lesson("LES-0001", teacher, "2026-09-10", 45, LessonType.INDIVIDUAL,
                           students=[("STU-0001", "Петров Петя")])
        pay_repo = PaymentRepoFake()
        api_payment = _api_payment(student_id="STU-0001", period="2026-09", amount="1000.00")

        svc_before_restart = PaymentService(pay_repo, LessonRepoFake([lesson]), TeacherRepoFake([teacher]))
        svc_after_restart = PaymentService(pay_repo, LessonRepoFake([lesson]), TeacherRepoFake([teacher]))

        async def scenario():
            await process_yookassa_event(
                _event("pay-restart-1"), svc_before_restart, bot=None, user_repo=_FakeUserRepo(),
                fetch_payment=_fetch_returning(api_payment),
            )
            await process_yookassa_event(
                _event("pay-restart-1"), svc_after_restart, bot=None, user_repo=_FakeUserRepo(),
                fetch_payment=_fetch_returning(api_payment),
            )

        _run(scenario())
        paid_rows = [p for p in _run(pay_repo.get_all()) if p.status == PaymentStatus.PAID]
        assert len(paid_rows) == 1
        assert any("yk:pay-restart-1" in e.ref.split() for e in feed.items)
    finally:
        activity.setup(None)


def test_failed_record_payment_is_not_marked_settled_and_can_retry():
    """Если запись оплаты упала с ошибкой, payment_id не помечается обработанным и повтор срабатывает."""
    class _FlakyPaymentService(_FakePaymentService):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def record_payment(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("sheets unavailable")
            return await super().record_payment(*args, **kwargs)

    svc = _FlakyPaymentService()
    api_payment = _api_payment(student_id="STU-0001", period="2026-09", amount="1000.00")

    async def scenario():
        try:
            await process_yookassa_event(
                _event("pay-flaky-1"), svc, bot=None, user_repo=_FakeUserRepo(),
                fetch_payment=_fetch_returning(api_payment),
            )
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass
        return await process_yookassa_event(
            _event("pay-flaky-1"), svc, bot=None, user_repo=_FakeUserRepo(),
            fetch_payment=_fetch_returning(api_payment),
        )

    assert _run(scenario()) == 200
    assert svc.confirmed == [("STU-0001", "2026-09")]
