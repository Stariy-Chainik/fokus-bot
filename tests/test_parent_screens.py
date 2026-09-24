"""Экраны родителя (bot/screens) и данные (bot/services/parent_views) — общие для TG и MAX."""
import asyncio
from types import SimpleNamespace

from bot.screens.parent_menu import menu_rows, welcome_text
from bot.screens.parent_bills import (
    bills_list_screen, bill_detail_screen, teacher_select_screen, methods_screen,
    bank_screen, online_pay_screen, student_select_screen, cash_screen,
)
from bot.services.parent_views import (
    PeriodRow, BillDetail, bill_detail, breakdown_lines, admin_confirm_rows, receipt_caption, period_label,
    selected_from, selection_fsm_data,
)
from bot.services.payment_ledger import BillAggregate, TeacherLedger


def _payloads(rows):
    return [[(b.label, b.kind, b.value) for b in row] for row in rows]


def test_menu_rows_by_platform():
    tg = [b.value for row in menu_rows(can_switch_athlete=True) for b in row]
    assert tg == ["client:lessons", "client:my_bills", "client:diary", "client:add_child", "client:email", "mode:athlete"]
    mx = [b.value for row in menu_rows(platform="max") for b in row]
    assert mx == ["client:my_bills", "client:add_child"]
    # PARENT_RECEIPT_EMAIL=false: кнопки «Email для чеков» нет, остальное на месте
    off = [b.value for row in menu_rows(can_switch_athlete=True, receipt_email=False) for b in row]
    assert off == ["client:lessons", "client:my_bills", "client:diary", "client:add_child", "mode:athlete"]
    assert "Ребёнок: <b>Иванов</b>" in welcome_text([SimpleNamespace(name="Иванов")])


def test_bills_list_screen_recent_and_older():
    rows = [PeriodRow("2026-09", "Сентябрь 2026 — 1000 руб. (текущий)", "📅")]
    text, kb = bills_list_screen(rows, "STU-1", "Иванов", show_older=False, show_back=True)
    assert text == "<b>💳 Оплата занятий — Иванов</b>"
    assert _payloads(kb) == [
        [("📅 Сентябрь 2026 — 1000 руб. (текущий)", "cb", "client_bill:STU-1:2026-09")],
        [("📆 Другие месяцы", "cb", "cl_bills_more:STU-1")],
        [("« Назад", "cb", "client:my_bills")],
        [("« Меню", "cb", "go:home")],
    ]
    text, kb = bills_list_screen([], "STU-1", "Иванов", show_older=True, show_back=False)
    assert text.startswith("📋 За более ранние")
    assert _payloads(kb) == [[("« К текущим месяцам", "cb", "cl_bills_stu:STU-1")], [("« Меню", "cb", "go:home")]]


def test_bill_detail_screen_pay_button_only_when_can_pay():
    d = BillDetail(lines=["x"], grand_total=100, unpaid_total=100)
    assert d.can_pay
    _, kb = bill_detail_screen(d, "2026-09", "STU-1")
    assert kb[0][0].value == "client_pay:STU-1:2026-09"
    d.unpaid_total = 0
    _, kb = bill_detail_screen(d, "2026-09", "STU-1")
    assert kb[0][0].value == "cl_bills_stu:STU-1"


def _student(name: str = "Алиса", student_id: str = "STU-0001"):
    return SimpleNamespace(name=name, student_id=student_id)


def test_bill_detail_shows_unpaid_lessons_first_and_paid_lessons_below():
    items = [
        SimpleNamespace(lesson_id="LES-1", date="2026-09-05", duration_min=60, amount=1300),
        SimpleNamespace(lesson_id="LES-2", date="2026-09-12", duration_min=60, amount=1300),
        SimpleNamespace(lesson_id="LES-3", date="2026-09-19", duration_min=60, amount=1300),
    ]
    ledger = TeacherLedger(
        "T1", "Мария Иванова", accrued=3900, paid=2600, items=items,
        pending=SimpleNamespace(payment_id="PAY-000001"),
    )

    class _Service:
        async def ledger_for(self, student, period_month):
            return {"T1": ledger}

        async def direct_pay_rows(self, student_id, period_month):
            return []

    detail = asyncio.run(bill_detail([_student()], "2026-09", _Service()))
    text = "\n".join(detail.lines)

    assert "<b>⬜ К оплате:</b>" in text
    assert "⬜ 19.09.2026  60 мин  — 1300 руб." in text
    assert "<b>✅ Оплачено:</b>" in text
    assert "✅ 05.09.2026  60 мин  — 1300 руб." in text
    assert text.index("⬜ 19.09.2026") < text.index("✅ 05.09.2026")
    assert "частич" not in text.lower()
    assert detail.unpaid_total == 1300


def test_bill_detail_subscription_has_binary_status_only():
    ledger = TeacherLedger(
        "SUB:G1", "Абонемент", accrued=7000, paid=0, subscription=True,
        pending=SimpleNamespace(payment_id="PAY-000002"),
    )

    class _Service:
        async def ledger_for(self, student, period_month):
            return {"SUB:G1": ledger}

        async def direct_pay_rows(self, student_id, period_month):
            return []

    detail = asyncio.run(bill_detail([_student()], "2026-09", _Service()))
    text = "\n".join(detail.lines)

    assert "<b>💳 Абонемент ⬜</b>" in text
    assert "частич" not in text.lower()


def test_teacher_select_and_methods():
    unpaid = [{"tid": "T1", "name": "Река", "amount": 1000, "pid": 5}, {"tid": "T2", "name": "Абонемент", "amount": 7000, "pid": 6}]
    text, kb = teacher_select_screen("STU-1", "2026-09", "Иванов", unpaid, {"T1"})
    assert "Оплата за Сентябрь 2026 — Иванов" in text
    assert _payloads(kb)[0] == [("✅ Река — 1000 руб.", "cb", "pselt:0")]
    assert _payloads(kb)[1] == [("⬜ Абонемент — 7000 руб.", "cb", "pselt:1")]
    assert _payloads(kb)[2] == [("➡️ К оплате: 1000 руб.", "cb", "pselgo")]
    text, kb = methods_screen("STU-1", "2026-09", "Иванов", unpaid, yookassa=True)
    assert "Сумма: <b>8000 руб.</b>" in text
    assert [b.value for row in kb for b in row] == [       # порядок: реквизиты → СБП онлайн
        "pay_method:bank:STU-1:2026-09", "pay_method:ysbp:STU-1:2026-09", "client_bill:STU-1:2026-09",
    ]
    text, kb = methods_screen("STU-1", "2026-09", "", unpaid, yookassa=False)
    assert kb[0][0].value == "pay_method:bank:STU-1:2026-09"


def test_methods_screen_with_cash():
    """Порядок способов: наличные → по реквизитам с чеком → СБП онлайн (решение владельца 21.09.2026)."""
    unpaid = [{"tid": "T1", "name": "Река", "amount": 1000, "pid": 5}]
    _, kb = methods_screen("STU-1", "2026-09", "Иванов", unpaid, yookassa=True, cash=True)
    assert [b.value for row in kb for b in row] == [
        "pay_method:cash:STU-1:2026-09",
        "pay_method:bank:STU-1:2026-09",
        "pay_method:ysbp:STU-1:2026-09",
        "client_bill:STU-1:2026-09",
    ]


def test_selection_helpers():
    unpaid = [{"tid": "T1", "name": "A", "amount": 10, "pid": 1}, {"tid": "T2", "name": "B", "amount": 20, "pid": 0}]
    assert selected_from({}, "S", "2026-09", unpaid) == unpaid
    assert selected_from({"pay_sel_key": "S:2026-09", "pay_sel": ["T2"]}, "S", "2026-09", unpaid) == [unpaid[1]]
    d = selection_fsm_data([unpaid[0]], unpaid)
    assert d == {"receipt_sel_pids": "1", "receipt_sel_label": "A", "receipt_sel_total": 10,
                 "receipt_sel_partial": True, "receipt_sel_tids": ["T1"]}


def test_bank_online_cash_screens():
    text, kb = bank_screen(800, "STU-1", "2026-09", "Сбер\\nкарта 1234")
    assert "Сбер\nкарта 1234" in text and kb[0][0].value == "receipt_upload:bank:STU-1:2026-09"
    text, kb = online_pay_screen("ysbp", 800, "https://pay", "STU-1", "2026-09")
    assert kb[0][0].kind == "url" and kb[0][0].value == "https://pay" and "СБП" in text
    _, kb = cash_screen(800, "STU-1", "2026-09")
    assert kb[0][0].value == "cash_notify:STU-1:2026-09"
    text, kb = student_select_screen([SimpleNamespace(name="А", student_id="STU-1")], "bills")
    assert [b.value for row in kb for b in row] == ["cl_bills_stu:STU-1", "cl_bills_stu:all", "go:home"]


def test_admin_confirm_rows_and_caption():
    rows = admin_confirm_rows("STU-1", "2026-09", "5.6", True, ("max", 42))
    assert [b.value for row in rows for b in row] == ["rcpp:STU-1:2026-09:5.6:a", "rcpt_no:STU-1:2026-09:m42"]
    rows = admin_confirm_rows("STU-1", "2026-09", "", False, ("tg", 7))
    assert [b.value for row in rows for b in row] == ["receipt_confirm:STU-1:2026-09:a", "rcpt_no:STU-1:2026-09:7"]
    rows = admin_confirm_rows("STU-1", "2026-09", "5", True, ("tg", 7), 1300, "cash")
    assert rows[0][0].value == "rcpp:STU-1:2026-09:5:1300:c"
    rows = admin_confirm_rows("STU-1", "2026-09", "", False, ("tg", 7), 1300, "bank")
    assert rows[0][0].value == "receipt_confirm:STU-1:2026-09:1300:b"
    cap = receipt_caption("bank", "Иванов", "2026-09", 800, "• Река — 800 руб.")
    assert cap.startswith("📎 Чек об оплате\n\nСпособ: 🏦 По реквизитам\nУченик: Иванов\nПериод: Сентябрь 2026\nСумма: 800 руб.")
    assert period_label("2026-01") == "Январь 2026"


def test_breakdown_lines_short_when_too_long():
    items = [SimpleNamespace(date=f"2026-09-{d:02d}", duration_min=60, lesson_type="group") for d in range(1, 30)]
    bills = {"T1": BillAggregate("Река", 100, items=items)}
    full = breakdown_lines(bills, ["T1"], limit=10_000)
    short = breakdown_lines(bills, ["T1"], limit=50)
    assert full[0].startswith("• Река — 100 руб.: 01.09 (60м, группа)")
    assert short == ["• Река — 100 руб. (29 зан.)"]


def test_admin_lesson_selection_screen():
    """Экран админа «отметить занятия»: оплаченные — без кнопки, выбранные считаются в сумму."""
    from bot.handlers.admin.bills.confirm import _sel_screen
    from bot.services.payment_ledger import TeacherLedger
    ledger = TeacherLedger("TCH-1", "Река Станислав", accrued=7917, paid=2500)
    marks = [
        {"lesson_id": "LES-1", "date": "2026-09-01", "duration_min": 90, "amount": 2500, "paid": True},
        {"lesson_id": "LES-2", "date": "2026-09-04", "duration_min": 90, "amount": 2500, "paid": False},
        {"lesson_id": "LES-3", "date": "2026-09-11", "duration_min": 60, "amount": 1667, "paid": False},
    ]
    text, rows, total = _sel_screen("Зотов Антон", ledger, marks, {"LES-3"})
    assert "Начислено 7917 руб., оплачено 2500, к доплате 5417" in text
    labels = [(r[0].text, r[0].callback_data) for r in rows]
    assert labels[0][1] == "noop" and labels[0][0].startswith("✅")
    assert labels[1][0].startswith("⬜") and labels[1][1] == "pslt:1"
    assert labels[2][0].startswith("☑️") and labels[2][1] == "pslt:2"
    assert labels[3] == ("✅ Подтвердить оплату 1667 руб.", "pslgo")
    assert total == 1667
    # ничего не выбрано — кнопки подтверждения нет
    _, rows_empty, total_empty = _sel_screen("Зотов Антон", ledger, marks, set())
    assert total_empty == 0 and all(r[0].callback_data != "pslgo" for r in rows_empty)


def test_bill_detail_shows_direct_pay_teacher_outside_the_total():
    """Педагог с прямой оплатой виден в счёте бота так же, как в кабинете, но вне «К оплате»."""
    from bot.services.payment_ledger import DirectPayLesson, DirectPayRow

    ledger = TeacherLedger("T1", "Мария Иванова", accrued=1300, paid=0,
                           items=[SimpleNamespace(lesson_id="LES-1", date="2026-09-05",
                                                  duration_min=60, amount=1300)],
                           pending=SimpleNamespace(payment_id="PAY-000003"))
    direct = DirectPayRow("T2", "Клецова Ангелина", 2334, [
        DirectPayLesson("LES-9", "2026-09-02", 60, 1334),
        DirectPayLesson("LES-10", "2026-09-09", 45, 1000),
    ])

    class _Service:
        async def ledger_for(self, student, period_month):
            return {"T1": ledger}

        async def direct_pay_rows(self, student_id, period_month):
            return [direct]

    detail = asyncio.run(bill_detail([_student()], "2026-09", _Service()))
    text = "\n".join(detail.lines)

    assert "Клецова Ангелина" in text and "02.09.2026  60 мин  — 1334 руб." in text
    assert "оплачивается педагогу напрямую" in text
    assert detail.direct_total == 2334
    assert detail.grand_total == 1300 and detail.unpaid_total == 1300   # прямая оплата вне сумм школы
    assert "К оплате: 1300 руб." in text


def test_bill_detail_with_only_direct_lessons_is_not_empty():
    """Месяц, где у ребёнка только занятия с прямой оплатой, не выглядит пустым."""
    from bot.services.payment_ledger import DirectPayLesson, DirectPayRow

    class _Service:
        async def ledger_for(self, student, period_month):
            return {}

        async def direct_pay_rows(self, student_id, period_month):
            return [DirectPayRow("T2", "Клецова Ангелина", 1000,
                                 [DirectPayLesson("LES-9", "2026-09-02", 45, 1000)])]

    detail = asyncio.run(bill_detail([_student()], "2026-09", _Service()))
    text = "\n".join(detail.lines)
    assert "Занятий не найдено" not in text and "Клецова Ангелина" in text
    assert detail.can_pay is False
