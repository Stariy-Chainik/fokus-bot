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
