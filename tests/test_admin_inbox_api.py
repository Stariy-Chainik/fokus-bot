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


def test_request_snapshot_keeps_money_on_lessons_known_at_request_time(api, monkeypatch):
    """Случай Ким Алины 25.09.2026: родитель заявил наличные за остаток, педагог после этого отметил
    ещё занятие — подтверждение зачитывает ровно то, что было в заявке; новое занятие остаётся долгом."""
    from bot.services import pending_queue
    from tests.fakes import mk_lesson, mk_teacher
    app, dp = api
    pending_queue.setup(dp["payment_service"])
    other = mk_teacher("TCH-0002", "Никишин Влад", rate_group=1000, rate_for_teacher=1500, rate_for_student=1600)
    dp["teacher_repo"].items.append(other)
    s = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    # остаток Иванова у Реки: 4800 (4000 инд. + 800 группа) — родитель заявляет ровно его
    action = asyncio.run(queue_action(dp["pending_repo"], KIND_CASH, s, YM, amount=4800, method="cash",
                                      parent_addr=str(PARENT_TG)))
    assert action.teacher_keys == "TCH-0001=4800" and action.key_amounts == {"TCH-0001": 4800}
    # после заявки отмечено занятие другого педагога на 1600
    dp["lesson_repo"].items.append(mk_lesson("LES-NEW", other, f"{YM}-27", students=[("STU-0001", "Иванов Иван")]))
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["credited"] == 4800
    ledgers = asyncio.run(dp["payment_service"].ledger_for(s, YM))
    assert ledgers["TCH-0001"].remainder == 0 and ledgers["TCH-0002"].remainder == 1600   # долг — на новом занятии
    # снимок снят и для заявки по выбранным педагогам: ключи без сумм по-прежнему читаются
    a2 = asyncio.run(queue_action(dp["pending_repo"], KIND_CASH, s, YM, amount=1600, method="cash",
                                  parent_addr=str(PARENT_TG), teacher_keys=["TCH-0002"]))
    assert a2.keys == ["TCH-0002"] and a2.key_amounts == {"TCH-0002": 1600}


def _unlink_action(dp, target="700002"):
    s = dp["student_repo"].items[0]
    s.parent_tg_ids = [PARENT_TG, 700002]
    return asyncio.run(dp["pending_repo"].add("unlink", "STU-0001", "Иванов Иван", parent_addr=str(PARENT_TG),
                                              comment="отвязать: Олег (Telegram) · просит: Мария",
                                              teacher_keys=target))


def test_unlink_request_approved_in_cabinet(api):
    """Заявка родителя «отвязать другого родителя»: в очереди с кнопками «Отвязать / Оставить»;
    одобрение снимает привязку, обоим родителям — сообщение."""
    from tests.test_admin_api import NotifierFake

    class Notifier(NotifierFake):
        async def send(self, addr, text, rows=None):
            self.sent.append(([addr], text))
            return True
    app, dp = api
    dp["notifier"] = Notifier()
    action = _unlink_action(dp)
    item = _call(app, "GET", "/api/admin/inbox")[1]["items"][0]
    assert (item["kind"], item["approveLabel"], item["rejectLabel"]) == ("unlink", "✅ Отвязать", "❌ Оставить")
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})
    assert status == 200 and r["status"] == DONE
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0001")).parent_tg_ids == [PARENT_TG]
    assert [(to, "одобрил" in t or "закрыл вам доступ" in t) for to, t in dp["notifier"].sent] == [
        ([("tg", PARENT_TG)], True), ([("tg", 700002)], True)]
    assert _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": True})[0] == 409


def test_unlink_request_rejected_keeps_parent(api):
    app, dp = api
    action = _unlink_action(dp)
    status, r = _call(app, "POST", f"/api/admin/inbox/{action.action_id}/decide", json={"approve": False})
    assert status == 200 and r["status"] == REJECTED
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0001")).parent_tg_ids == [PARENT_TG, 700002]


def test_unlink_drops_client_address_unless_sibling_keeps_it():
    """Уведомления идут и на адрес карточки клиента: после отвязки он снимается с карточки,
    если этот родитель не остался у другого ребёнка той же семьи."""
    from bot.services.parent_unlink import approve_unlink
    from tests.fakes import ClientRepoWritable, StudentRepoWritable, mk_client, mk_student
    for sibling_linked, expected in ((False, None), (True, 700002)):
        students = StudentRepoWritable([
            mk_student("STU-0001", "Иванов Иван", parent_tg_ids=[PARENT_TG, 700002], client_id="CLT-0001"),
            mk_student("STU-0003", "Иванова Ева", parent_tg_ids=[700002] if sibling_linked else [], client_id="CLT-0001"),
        ])
        clients = ClientRepoWritable([mk_client("CLT-0001", tg_id=700002)])
        pending = PendingRepoFake()
        action = asyncio.run(pending.add("unlink", "STU-0001", "Иванов Иван", parent_addr=str(PARENT_TG),
                                         teacher_keys="700002"))
        assert asyncio.run(approve_unlink(pending, students, clients, None, action, ADMIN_TG)) is not None
        assert asyncio.run(clients.get_by_id("CLT-0001")).tg_id == expected
        assert asyncio.run(students.get_by_id("STU-0001")).parent_tg_ids == [PARENT_TG]
