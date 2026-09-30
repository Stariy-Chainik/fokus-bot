"""Очередь решений администратора (/api/admin/inbox): что ждёт решения и как оно применяется."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api.admin import register_admin_api
from bot.models.enums import PaymentStatus
from bot.repositories.pending_action_repo import DONE, KIND_CASH, KIND_CHILD, OPEN, REJECTED
from bot.services.pending_queue import close_actions, queue_action
from tests.fakes import mk_payment
from tests.test_admin_api import ADMIN_TG, PARENT_TG, YM, make_api
from tests.test_telegram_auth import make_init_data


class PendingRepoFake:
    """Лист pending_actions в памяти (bot/repositories/pending_action_repo.py)."""

    def __init__(self) -> None:
        self.items: list = []

    async def get_all(self):
        return list(self.items)

    async def get_open(self):
        return [a for a in self.items if a.status == OPEN]

    async def get_by_id(self, action_id):
        return next((a for a in self.items if a.action_id == action_id), None)

    async def add(self, kind, student_id, student_name, period_month="", amount=0, method="",
                  parent_addr="", file_id="", file_type="", comment="", teacher_keys="", held_by=""):
        from bot.repositories.pending_action_repo import PendingAction
        a = PendingAction(f"ACT-{len(self.items) + 1:06d}", kind, student_id, student_name,
                          period_month, amount, method, parent_addr, file_id, file_type,
                          comment, "2026-09-20 10:00:00", teacher_keys=teacher_keys, held_by=held_by)
        self.items.append(a)
        return a

    async def set_held_by(self, action_id, teacher_id):
        a = await self.get_by_id(action_id)
        if a is None or a.status != OPEN:
            return False
        a.held_by = teacher_id
        return True

    async def claim(self, action_id, status, decided_by_tg_id=0):
        a = await self.get_by_id(action_id)
        if a is None or a.status != OPEN:
            return False
        a.status, a.decided_by_tg_id = status, decided_by_tg_id
        return True

    async def close(self, action_id, status, decided_by_tg_id=0):
        a = await self.get_by_id(action_id)
        if a is None:
            return False
        a.status, a.decided_by_tg_id = status, decided_by_tg_id
        return True

    async def close_for_period(self, student_id, period_month, status, decided_by_tg_id=0, kinds=()):
        n = 0
        for a in await self.get_open():
            if (a.student_id == student_id and a.period_month == period_month and (not kinds or a.kind in kinds)
                    and not a.held_by):
                await self.close(a.action_id, status, decided_by_tg_id)
                n += 1
        return n


def _call(dp, method, path, tg_id=ADMIN_TG, json=None):
    async def run():
        app = web.Application()
        register_admin_api(app, dp)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            headers = {"Authorization": f"tma {make_init_data(user_id=tg_id)}"}
            resp = await client.request(method, path, headers=headers, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


@pytest.fixture()
def api(monkeypatch):
    dp, _ = make_api(monkeypatch)
    dp["pending_repo"] = PendingRepoFake()
    return dp, dp


def test_queue_shows_open_cash_with_current_rest(api):
    app, dp = api
    asyncio.run(queue_action(dp["pending_repo"], KIND_CASH, None, YM, amount=2000,
                             method="cash", parent_addr=str(PARENT_TG),
                             student_id="STU-0001", student_name="Иванов Иван"))
    status, d = _call(app, "GET", "/api/admin/inbox")
    assert status == 200 and d["total"] == 1
    item = d["items"][0]
    assert item["kind"] == KIND_CASH and item["amount"] == 2000 and item["student"] == "Иванов Иван"
    assert item["rest"] == 4800 and not item["hasFile"]      # остаток по счёту на сейчас


def test_approve_credits_the_payment_and_closes_the_row(api):
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash",
                                                str(PARENT_TG)))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["credited"] == 2000
    assert dp["pending_repo"].items[0].status == DONE
    paid = [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert sum(p.total_amount for p in paid) == 2000
    assert _call(app, "GET", "/api/admin/inbox")[1]["total"] == 0      # очередь опустела


def test_reject_closes_without_payment(api):
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash",
                                                str(PARENT_TG)))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": False})
    assert status == 200 and r["status"] == REJECTED
    assert not [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]


def test_child_request_links_the_parent(api):
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CHILD, "STU-0002", "Петрова Анна", parent_addr="777"))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["status"] == DONE
    student = asyncio.run(dp["student_repo"].get_by_id("STU-0002"))
    assert ("tg", 777) in student.parent_addrs


def test_decision_in_chat_closes_the_queue_row(api):
    """Подтверждение кнопкой в Telegram закрывает ту же строку — очередь не задваивается."""
    app, dp = api
    asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash"))
    asyncio.run(close_actions(dp["pending_repo"], "STU-0001", YM, DONE, ADMIN_TG))
    assert _call(app, "GET", "/api/admin/inbox")[1]["total"] == 0


def test_file_endpoint_needs_a_receipt(api):
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash"))
    assert _call(app, "GET", f"/api/admin/inbox/{action.action_id}/file")[0] == 503   # бота нет в тестах
    assert _call(app, "POST", "/api/admin/inbox/ACT-999999/decide", json={"approve": True})[0] == 404


def test_second_decision_is_refused(api):
    """Повторное подтверждение (второе сообщение в чате, двойной тап) ничего не зачитывает."""
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 2000, "cash"))
    first = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert first[0] == 200 and first[1]["credited"] == 2000
    second = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert second[0] == 409 and second[1]["error"] == "already_decided"
    paid = [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert sum(p.total_amount for p in paid) == 2000          # зачтено ровно один раз


def test_amount_over_the_rest_asks_first(api):
    """Заявлено больше остатка — сервер не проводит молча, а спрашивает про переплату."""
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 9000, "cash"))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 409 and r["needsConfirm"] and r["rest"] == 4800 and r["amount"] == 9000
    assert dp["pending_repo"].items[0].status == OPEN         # решение не занято, можно вернуться

    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide",
                      json={"approve": True, "amount": 4800, "force": True})
    assert status == 200 and r["credited"] == 4800 and r["overpaid"] == 0


def test_overpay_goes_through_when_confirmed(api):
    app, dp = api
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 9000, "cash"))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide",
                      json={"approve": True, "amount": 9000, "force": True})
    assert status == 200 and r["credited"] == 9000 and r["overpaid"] == 4200


def test_buttons_carry_the_action_id(api):
    """Уведомление админу ссылается на строку очереди — все копии ведут к одному решению."""
    from bot.services.parent_views import admin_confirm_rows
    rows = admin_confirm_rows("STU-0001", YM, "185.186", True, ("tg", PARENT_TG), 2000, "cash",
                              action_id="ACT-000007")
    payloads = [b.value for row in rows for b in row]
    assert payloads[0].startswith("pact:ACT-000007:2000:") and payloads[1] == "pnay:ACT-000007"
    # без очереди (старые сообщения) — прежние кнопки
    legacy = admin_confirm_rows("STU-0001", YM, "185.186", True, ("tg", PARENT_TG), 2000, "cash")
    assert legacy[0][0].value.startswith("rcpp:STU-0001")


def test_zero_amount_closes_the_row_without_credit(api):
    """Остатка нет (оплату уже отметили вручную): «закрыть заявку» не зачитывает заявленную сумму."""
    app, dp = api
    dp["payment_repo"].rows.append(mk_payment("PAY-1", "STU-0001", YM, "TCH-0001", 4800, status=PaymentStatus.PAID))
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 4800, "cash", str(PARENT_TG)))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 409 and r["needsConfirm"] and r["rest"] == 0
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide",
                      json={"approve": True, "amount": 0, "force": True})
    assert status == 200 and r["credited"] == 0 and r["status"] == DONE
    paid = [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert sum(p.total_amount for p in paid) == 4800                # ничего не добавилось


def test_approval_credits_the_teacher_the_parent_paid_for(api):
    """Родитель платил за второго педагога: зачитывается ему, а не первому по алфавиту."""
    from tests.fakes import mk_lesson, mk_teacher
    app, dp = api
    second = mk_teacher("TCH-0002", "Аверин Пётр", rate_group=1000, rate_for_teacher=1500, rate_for_student=3000)
    dp["teacher_repo"].items.append(second)
    dp["lesson_repo"].items.append(mk_lesson("LES-A", second, f"{YM}-15", students=[("STU-0001", "Иванов Иван")]))
    action = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 3000, "cash",
                                                str(PARENT_TG), teacher_keys="TCH-0002"))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["credited"] == 3000
    paid = [(p.teacher_id, p.total_amount) for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert paid == [("TCH-0002", 3000)]                              # Река (4800) не тронут
    # заявлено больше остатка выбранного педагога — 409 считается по нему, а не по всем
    action2 = asyncio.run(dp["pending_repo"].add(KIND_CASH, "STU-0001", "Иванов Иван", YM, 1000, "cash",
                                                 str(PARENT_TG), teacher_keys="TCH-0002"))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action2.action_id}/decide", json={"approve": True})
    assert status == 409 and r["rest"] == 0
