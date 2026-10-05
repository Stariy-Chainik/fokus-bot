"""Второй родитель к ребёнку — только с одобрения администратора."""
import asyncio
from types import SimpleNamespace

from bot.handlers.client.group_link import cb_group_link_pick
from bot.handlers.client.start import cb_client_reg_confirm
from bot.services.parent_linking import needs_approval
from tests.fakes import FakeCallbackQuery, FakeState
from tests.test_admin_api import ADMIN_TG, PARENT_TG, make_api
from tests.test_admin_inbox_api import PendingRepoFake

NEW_PARENT = 555001


def test_needs_approval_only_when_someone_else_is_linked():
    s = SimpleNamespace(parent_addrs=[("tg", PARENT_TG)])
    assert needs_approval(s, ("tg", NEW_PARENT)) and not needs_approval(s, ("tg", PARENT_TG))
    assert not needs_approval(SimpleNamespace(parent_addrs=[]), ("tg", NEW_PARENT))


def _run(handler, data, dp, **extra):
    cb = FakeCallbackQuery(data, user_id=NEW_PARENT)
    cb.from_user.full_name = "Мама Иванова"
    asyncio.run(handler(cb, **extra))
    return cb


def test_second_parent_by_surname_goes_to_admin(monkeypatch):
    dp, _ = make_api(monkeypatch)
    pending = PendingRepoFake()
    cb = _run(cb_client_reg_confirm, "client_reg:STU-0001", dp,
              student_repo=dp["student_repo"], user_repo=dp["user_repo"], pending_repo=pending)
    s = asyncio.run(dp["student_repo"].get_by_id("STU-0001"))
    assert NEW_PARENT not in s.parent_tg_ids                           # не привязан сам
    assert [(a.kind, a.student_id, a.parent_addr) for a in pending.items] == [("child", "STU-0001", str(NEW_PARENT))]
    sent = [x for x in cb.bot.sent if x[0] == ADMIN_TG]
    assert sent and "Второй родитель" in sent[0][1]
    # первый родитель привязывается сам, как раньше
    cb = _run(cb_client_reg_confirm, "client_reg:STU-0002", dp,
              student_repo=dp["student_repo"], user_repo=dp["user_repo"], pending_repo=pending)
    assert NEW_PARENT in asyncio.run(dp["student_repo"].get_by_id("STU-0002")).parent_tg_ids


def test_second_parent_by_group_link_goes_to_admin(monkeypatch):
    dp, _ = make_api(monkeypatch)
    pending = PendingRepoFake()
    _run(cb_group_link_pick, "glink:GRP-0001:STU-0001", dp, state=FakeState(), student_repo=dp["student_repo"],
         group_repo=dp["group_repo"], user_repo=dp["user_repo"], pending_repo=pending)
    assert NEW_PARENT not in asyncio.run(dp["student_repo"].get_by_id("STU-0001")).parent_tg_ids
    assert pending.items and pending.items[0].kind == "child"


# ─── «Моего ребёнка нет в списке» → карточку заводит педагог/админ (решение владельца 05.10.2026) ──

def test_child_missing_in_group_creates_request_and_teacher_approves(monkeypatch):
    from bot.handlers.admin.client_requests import cb_new_child_ok, cb_new_child_no
    from bot.handlers.client.group_link import cb_group_link_none, msg_group_link_child_name
    from bot.services.new_child import KIND_NEWCHILD
    from tests.fakes import FakeMessage

    dp, _ = make_api(monkeypatch)
    pending = PendingRepoFake()
    teacher_tg = 777001                                               # педагог группы со своим Telegram
    dp["teacher_repo"].items[0].tg_id = teacher_tg
    teacher_user = asyncio.run(dp["user_repo"].add(teacher_tg, teacher_id="TCH-0001"))
    # родитель: «нет в списке» → ввод имени
    cb = FakeCallbackQuery("glink_none:GRP-0001", user_id=NEW_PARENT)
    state = FakeState()
    asyncio.run(cb_group_link_none(cb, state, group_repo=dp["group_repo"]))
    assert "фамилию и имя" in cb.message.screens[-1][0]
    msg = FakeMessage("  Сидорова   Мария ", user_id=NEW_PARENT)
    msg.from_user.full_name = "Папа Сидоров"
    asyncio.run(msg_group_link_child_name(
        msg, state, group_repo=dp["group_repo"], user_repo=dp["user_repo"], teacher_repo=dp["teacher_repo"],
        teacher_group_repo=dp["teacher_group_repo"], pending_repo=pending))
    a = pending.items[-1]
    assert (a.kind, a.student_id, a.student_name) == (KIND_NEWCHILD, "", "Сидорова Мария")
    assert (a.parent_addr, a.keys) == (str(NEW_PARENT), ["GRP-0001"])
    assert "Заявка отправлена" in msg.screens[-1][0]
    recipients = {x[0] for x in msg.bot.sent}
    assert ADMIN_TG in recipients and teacher_tg in recipients          # администратор и педагог группы
    assert "Сидорова Мария" in msg.bot.sent[0][1]
    before = {s.student_id for s in asyncio.run(dp["student_repo"].get_all())}

    # педагог группы одобряет кнопкой: карточка в группе, родитель привязан, ему ушло меню
    cb = FakeCallbackQuery(f"nchild_ok:{a.action_id}", user_id=teacher_tg)
    asyncio.run(cb_new_child_ok(cb, teacher_user, dp["student_repo"], student_group_repo=dp["student_group_repo"],
                                teacher_group_repo=dp["teacher_group_repo"], pending_repo=pending))
    new = [s for s in asyncio.run(dp["student_repo"].get_all()) if s.student_id not in before]
    assert len(new) == 1 and new[0].name == "Сидорова Мария" and NEW_PARENT in new[0].parent_tg_ids
    assert "GRP-0001" in asyncio.run(dp["student_group_repo"].get_groups_for_student(new[0].student_id))
    assert a.status == "done" and "Заведён ученик" in cb.message.screens[-1][0]
    assert any(x[0] == NEW_PARENT and "добавлен в группу" in x[1] for x in cb.bot.sent)
    # повторное решение — «уже решили»
    cb2 = FakeCallbackQuery(f"nchild_no:{a.action_id}", user_id=teacher_tg)
    asyncio.run(cb_new_child_no(cb2, teacher_user, teacher_group_repo=dp["teacher_group_repo"], pending_repo=pending))
    assert cb2.alerts[-1] == ("Заявку уже решили", True)


def test_new_child_request_visible_and_decidable_in_admin_inbox(monkeypatch):
    from bot.services.new_child import KIND_NEWCHILD
    from tests.test_admin_api import _call

    dp, app = make_api(monkeypatch)
    pending = PendingRepoFake()
    dp["pending_repo"] = pending
    asyncio.run(pending.add(KIND_NEWCHILD, "", "Петров Пётр", "", 0, "", str(NEW_PARENT), "", "", "Группа · Мама", "GRP-0001", ""))
    aid = pending.items[-1].action_id
    status, d = _call(app, "GET", "/api/admin/inbox")
    item = next(i for i in d["items"] if i["id"] == aid)
    assert status == 200 and item["kind"] == KIND_NEWCHILD and item["approveLabel"] == "✅ Завести и привязать"
    status, r = _call(app, "POST", f"/api/admin/inbox/{aid}/decide", json={"approve": True})
    assert status == 200 and r["studentId"].startswith("STU-")
    s = asyncio.run(dp["student_repo"].get_by_id(r["studentId"]))
    assert s.name == "Петров Пётр" and NEW_PARENT in s.parent_tg_ids
    assert _call(app, "POST", f"/api/admin/inbox/{aid}/decide", json={"approve": True})[0] == 409
