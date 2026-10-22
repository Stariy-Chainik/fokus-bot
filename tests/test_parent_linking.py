"""Второй родитель к ребёнку — только с одобрения администратора."""
import asyncio
from types import SimpleNamespace

from bot.handlers.client.group_link import cb_group_link_pick
from bot.handlers.client.start import cb_client_reg_confirm
from bot.services.parent_linking import needs_approval
from tests.fakes import FakeCallbackQuery, FakeState, mk_group
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
    # повтор (родитель жмёт ещё раз, пока заявка открыта): второй заявки и второго сообщения админу нет (16.10.2026)
    cb2 = _run(cb_client_reg_confirm, "client_reg:STU-0001", dp,
               student_repo=dp["student_repo"], user_repo=dp["user_repo"], pending_repo=pending)
    assert len(pending.items) == 1 and not [x for x in cb2.bot.sent if x[0] == ADMIN_TG]
    # одобрение: первая кнопка привязывает и уведомляет родителя, вторая (копия в чате) — «уже привязан», без уведомления
    from bot.handlers.admin.client_requests import cb_admin_child_ok
    from tests.fakes import mk_user
    admin = mk_user(ADMIN_TG, is_admin=True)
    ok1 = FakeCallbackQuery(f"admin_child_ok:{NEW_PARENT}:STU-0001", user_id=ADMIN_TG)
    asyncio.run(cb_admin_child_ok(ok1, admin, student_repo=dp["student_repo"], pending_repo=pending))
    assert NEW_PARENT in asyncio.run(dp["student_repo"].get_by_id("STU-0001")).parent_tg_ids
    assert all(a.status != "open" for a in pending.items)
    assert [x for x in ok1.bot.sent if x[0] == NEW_PARENT and "одобрена" in x[1]]
    ok2 = FakeCallbackQuery(f"admin_child_ok:{NEW_PARENT}:STU-0001", user_id=ADMIN_TG)
    asyncio.run(cb_admin_child_ok(ok2, admin, student_repo=dp["student_repo"], pending_repo=pending))
    assert not [x for x in ok2.bot.sent if x[0] == NEW_PARENT] and "Уже одобрено" in ok2.message.screens[-1][0]
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


def test_duplicate_new_child_request_is_not_repeated(monkeypatch):
    """Повторная заявка «нет в списке» на то же имя в ту же группу (из MAX и Telegram, дважды) — одна заявка,
    без второго сообщения администраторам (21.10.2026)."""
    from bot.services.new_child import KIND_NEWCHILD, request_new_child
    from tests.fakes import FakeBot

    dp, _ = make_api(monkeypatch)
    pending, bot = PendingRepoFake(), FakeBot()
    group = dp["group_repo"].items[0]
    args = (pending, bot, dp["user_repo"], dp["teacher_repo"], dp["teacher_group_repo"], group)
    first = asyncio.run(request_new_child(*args, "Сидорова Мария", ("tg", NEW_PARENT), "Папа"))
    assert first.duplicate is False and len(bot.sent) >= 1
    sent_before = len(bot.sent)
    again = asyncio.run(request_new_child(*args, "  сидорова   мария ", ("max", 555), "Мама в MAX"))
    assert again.duplicate is True and again.action.action_id == first.action.action_id
    assert len(pending.items) == 1 and len(bot.sent) == sent_before            # ни строки, ни сообщений
    # другая группа — отдельная заявка
    dp["group_repo"].items.append(mk_group("GRP-0002", "Другая"))
    other = asyncio.run(request_new_child(pending, bot, dp["user_repo"], dp["teacher_repo"], dp["teacher_group_repo"],
                                          dp["group_repo"].items[-1], "Сидорова Мария", ("tg", NEW_PARENT), "Папа"))
    assert other.duplicate is False and len(pending.items) == 2
    assert all(a.kind == KIND_NEWCHILD for a in pending.items)


def test_approve_links_parent_to_existing_student_instead_of_duplicate(monkeypatch):
    """Ученик с таким именем уже есть — одобрение не заводит вторую карточку: привязка к существующей,
    в группу добавляется, если его там нет; в «Ждут решения» подсказка и кнопка «Привязать к существующему»."""
    from bot.services.new_child import KIND_NEWCHILD
    from tests.test_admin_api import _call

    dp, app = make_api(monkeypatch)
    pending = PendingRepoFake()
    dp["pending_repo"] = pending
    # Петрова Анна (STU-0002) уже в базе, состоит в GRP-0001; заявка на «петрова анна» в GRP-0001
    asyncio.run(pending.add(KIND_NEWCHILD, "", "петрова   анна", "", 0, "", str(NEW_PARENT), "", "", "Группа · Мама", "GRP-0001", ""))
    aid = pending.items[-1].action_id
    item = next(i for i in _call(app, "GET", "/api/admin/inbox")[1]["items"] if i["id"] == aid)
    assert item["approveLabel"] == "✅ Привязать к существующему" and "STU-0002" in item["note"]
    before = len(asyncio.run(dp["student_repo"].get_all()))
    status, r = _call(app, "POST", f"/api/admin/inbox/{aid}/decide", json={"approve": True})
    assert status == 200 and r["studentId"] == "STU-0002" and r["existing"] is True
    assert len(asyncio.run(dp["student_repo"].get_all())) == before                    # второй карточки нет
    assert NEW_PARENT in asyncio.run(dp["student_repo"].get_by_id("STU-0002")).parent_tg_ids
    # тёзка не в этой группе — его добавят в группу, карточку не плодим
    dp["group_repo"].items.append(mk_group("GRP-0002", "Другая"))
    asyncio.run(pending.add(KIND_NEWCHILD, "", "Петрова Анна", "", 0, "", "m777", "", "", "Другая · Папа", "GRP-0002", ""))
    aid2 = pending.items[-1].action_id
    status, r = _call(app, "POST", f"/api/admin/inbox/{aid2}/decide", json={"approve": True})
    assert status == 200 and r["studentId"] == "STU-0002"
    assert "GRP-0002" in asyncio.run(dp["student_group_repo"].get_groups_for_student("STU-0002"))
    assert 777 in asyncio.run(dp["student_repo"].get_by_id("STU-0002")).parent_max_ids
    # незнакомое имя — новая карточка, как раньше
    asyncio.run(pending.add(KIND_NEWCHILD, "", "Новикова Вера", "", 0, "", str(NEW_PARENT), "", "", "Группа · Мама", "GRP-0001", ""))
    aid3 = pending.items[-1].action_id
    status, r = _call(app, "POST", f"/api/admin/inbox/{aid3}/decide", json={"approve": True})
    assert status == 200 and r["existing"] is False and len(asyncio.run(dp["student_repo"].get_all())) == before + 1
