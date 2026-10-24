"""Оплата закрепляется за занятиями на момент оплаты (решение владельца 24.10.2026, случай Звырлифус Бианки):
занятие, внесённое задним числом, не забирает деньги у уже оплаченного — оно висит неоплаченным."""
from types import SimpleNamespace

from bot.models import StudentPeriodPayment
from bot.models.enums import PaymentStatus
from bot.services.payment_ledger import lesson_paid_marks, linked_ids, norm_ts, pin_unlinked_rows


def _item(lid, date, amount=1800):
    return SimpleNamespace(lesson_id=lid, date=date, amount=amount, duration_min=60, lesson_type="group")


def _row(pid, amount, paid_at, lesson_ids=""):
    return StudentPeriodPayment(
        payment_id=pid, student_id="STU-1", student_name="—", period_month="2026-10", total_amount=amount,
        status=PaymentStatus.PAID, paid_at=paid_at, confirmed_by_tg_id=0, comment=None, created_at=paid_at,
        updated_at=paid_at, teacher_id="TCH-0011", teacher_name="—", lesson_ids=lesson_ids)


def _marks(items, rows):
    pinned = pin_unlinked_rows(items, rows, RECORDED)
    rows = [_row(r.payment_id, r.total_amount, r.paid_at, "|".join(pinned.get(r.payment_id, r.lesson_id_list)))
            for r in rows]
    ordered = sorted(items, key=lambda b: (b.date, b.lesson_id))
    linked = linked_ids(rows)
    paid = sum(r.total_amount for r in rows)
    return pinned, dict(zip([b.lesson_id for b in ordered],
                            lesson_paid_marks([b.amount for b in ordered], paid, [b.lesson_id in linked for b in ordered]),
                            strict=True))


# Бианка, октябрь: 02.10 (оплачено и привязано 05.10), 17.10 (оплата 23.10 без привязки),
# 10.10 на 900 — внесено задним числом 24.10, 24.10 — сегодня
RECORDED = {"L02": "2026-10-02 14:17:58", "L17": "2026-10-17 17:08:04",
            "L10": "2026-10-24 20:00:21", "L24": "2026-10-24 14:03:58"}
ITEMS = [_item("L02", "2026-10-02"), _item("L10", "2026-10-10", 900), _item("L17", "2026-10-17"), _item("L24", "2026-10-24")]


def test_backdated_lesson_does_not_take_paid_money():
    rows = [_row("P1", 1800, "2026-10-05 22:41:33", "L02"), _row("P2", 1800, "2026-10-23 16:11:35")]
    pinned, marks = _marks(ITEMS, rows)
    assert pinned == {"P2": ["L17"]}                                   # оплата 23.10 — за 17.10, а не за 10.10
    assert marks == {"L02": True, "L10": False, "L17": True, "L24": False}
    # раньше (без закрепления) 10.10 забирало деньги и 17.10 становилось неоплаченным
    ordered = sorted(ITEMS, key=lambda b: (b.date, b.lesson_id))
    old = lesson_paid_marks([b.amount for b in ordered], 3600, [b.lesson_id == "L02" for b in ordered])
    assert dict(zip([b.lesson_id for b in ordered], old, strict=True))["L17"] is False


def test_hour_without_leading_zero_compares_correctly():
    assert norm_ts("2026-10-02 8:22:44") == "2026-10-02 08:22:44" < norm_ts("2026-10-02 14:17:58")
    # оплата утром 02.10, занятие записано днём 02.10 — оплата не за него
    pinned = pin_unlinked_rows([_item("L02", "2026-10-02")], [_row("P1", 1800, "2026-10-02 8:22:44")],
                               {"L02": "2026-10-02 14:17:58"})
    assert pinned == {}


def test_prepayment_and_partial_payment_stay_in_pool():
    # оплатили вперёд 3600, когда было одно занятие: закреплено одно, остаток закроет следующее при показе
    rows = [_row("P1", 3600, "2026-10-03 10:00:00")]
    items = [_item("A", "2026-10-02"), _item("B", "2026-10-09")]
    pinned = pin_unlinked_rows(items, rows, {"A": "2026-10-02 12:00:00", "B": "2026-10-09 12:00:00"})
    assert pinned == {"P1": ["A"]}
    # частичная оплата 900 за занятие 1800 — ничего целиком не закрыла, строка без привязки
    assert pin_unlinked_rows([_item("A", "2026-10-02")], [_row("P1", 900, "2026-10-03 10:00:00")],
                             {"A": "2026-10-02 12:00:00"}) == {}


def test_two_payments_in_order_each_takes_existing_lessons():
    rows = [_row("P2", 1800, "2026-10-20 10:00:00"), _row("P1", 1800, "2026-10-05 10:00:00")]
    items = [_item("A", "2026-10-02"), _item("B", "2026-10-15"), _item("C", "2026-10-01")]
    # C (01.10) внесено задним числом 22.10 — после обеих оплат: им не достаётся
    recorded = {"A": "2026-10-02 12:00:00", "B": "2026-10-15 12:00:00", "C": "2026-10-22 12:00:00"}
    assert pin_unlinked_rows(items, rows, recorded) == {"P1": ["A"], "P2": ["B"]}
    # C внесено 18.10 — к оплате 20.10 оно уже было в счёте: оплата без выбора закрывает самое раннее
    recorded["C"] = "2026-10-18 12:00:00"
    assert pin_unlinked_rows(items, rows, recorded) == {"P1": ["A"], "P2": ["C"]}
