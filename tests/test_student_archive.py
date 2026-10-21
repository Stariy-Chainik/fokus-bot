"""«🗑 Удалить ученика» = архив (решение владельца 21.10.2026): один сценарий у администратора и педагога —
ученик уходит из школы (снят со всех групп, родители и партнёр отвязаны, в списках его нет, история остаётся,
в статистике «ушли»); долг показывается и требует подтверждения."""
import asyncio

from bot.models.enums import GroupBillingMode
from bot.services.student_archive import archive_student, student_debt, unarchive_student
from bot.utils.dates import current_period
from tests.fakes import FakeBot, mk_group
from tests.test_admin_api import ADMIN_TG, YM
from tests.test_admin_api import _call as _acall
from tests.test_teacher_api import _call, api  # noqa: F401 — педагог TCH-0001 с группой GRP-0001

CUR = current_period()


def _rows(dp, sid):
    return {x.group_id: x for x in asyncio.run(dp["student_group_repo"].get_all()) if x.student_id == sid}


def _student(dp, sid):
    return next(s for s in dp["student_repo"].items if s.student_id == sid)


def _prepare(dp):
    """Петрова: абонементная GRP-0001 (6000) + чужая группа GRP-0002 по посещению, родитель, партнёр Иванов."""
    g = dp["group_repo"].items[0]
    g.billing_mode, g.price_full = GroupBillingMode.SUBSCRIPTION, 6000
    dp["group_repo"].items.append(mk_group("GRP-0002", "ВБ ХГ Младшая", billing_mode=GroupBillingMode.PER_VISIT, price_full=800))
    asyncio.run(dp["student_group_repo"].add("STU-0002", "GRP-0002", "2026-04"))
    asyncio.run(dp["student_repo"].set_partner("STU-0002", "STU-0001"))
    asyncio.run(dp["student_repo"].add_parent("STU-0002", ("tg", 555)))


def _un(dp, sid):
    return asyncio.run(unarchive_student(sid, student_repo=dp["student_repo"], student_group_repo=dp["student_group_repo"]))


def test_debt_breakdown_and_archive_service(api):  # noqa: F811
    app, dp = api
    _prepare(dp)
    svc = dp["payment_service"]
    d = asyncio.run(student_debt(svc, "STU-0002", None))
    assert (d.total, d.sub, d.group, d.ind) == (6800, 6000, 800, 0)        # абонемент 6000 + групповое по посещению 800
    deps = dict(student_repo=dp["student_repo"], student_group_repo=dp["student_group_repo"], group_repo=dp["group_repo"],
                client_repo=dp["client_repo"], payment_service=svc, since=None)
    assert asyncio.run(archive_student("STU-0002", **deps)) == "debt"       # долг без подтверждения — стоп
    assert asyncio.run(archive_student("STU-0404", **deps)) == "not_found"
    r = asyncio.run(archive_student("STU-0002", **deps, confirm_debt=True, actor=ADMIN_TG, who="педагог Река"))
    assert [g for g, _n in r.groups] == ["GRP-0001", "GRP-0002"] and r.parents == 1 and r.partner == "Иванов Иван"
    rows = _rows(dp, "STU-0002")
    assert rows["GRP-0001"].left_period == CUR and rows["GRP-0002"].left_period == CUR     # строки остались, помечены
    s = _student(dp, "STU-0002")
    assert s.archived and s.parent_tg_ids == [] and s.partner_id is None
    assert _student(dp, "STU-0001").partner_id is None
    assert asyncio.run(archive_student("STU-0002", **deps, confirm_debt=True)) == "already"
    # архивного нет в списках и начислениях, но карточка по номеру открывается
    assert "STU-0002" not in {x.student_id for x in asyncio.run(dp["student_repo"].get_all())}
    assert asyncio.run(dp["student_repo"].get_by_id("STU-0002")).name == "Петрова Анна"
    assert "STU-0002" not in asyncio.run(svc.compute_debt_map())
    # возврат: архив снят, членства этого месяца восстановлены
    assert _un(dp, "STU-0002") is None
    assert not _student(dp, "STU-0002").archived and all(x.is_active for x in _rows(dp, "STU-0002").values())
    assert _un(dp, "STU-0002") == "not_archived"


def test_admin_archive_api_list_card_and_flow(api):  # noqa: F811
    app, dp = api
    _prepare(dp)
    st, p = _acall(app, "GET", "/api/admin/students/STU-0002/archive", tg_id=ADMIN_TG)
    assert st == 200 and [g["id"] for g in p["groups"]] == ["GRP-0001", "GRP-0002"]
    assert p["parents"] == 1 and p["partner"] == "Иванов Иван" and p["debt"]["total"] == 6800
    st, r = _acall(app, "POST", "/api/admin/students/STU-0002/archive", tg_id=ADMIN_TG, json={})
    assert st == 409 and r["error"] == "debt" and r["debt"]["total"] == 6800
    st, r = _acall(app, "POST", "/api/admin/students/STU-0002/archive", tg_id=ADMIN_TG, json={"confirmDebt": True})
    assert st == 200 and r["ok"] and r["period"] == CUR and r["parents"] == 1
    # список: без архивных; «📦 Архив» — только они; карточка — дата и ушедшие группы
    names = [x["name"] for x in _acall(app, "GET", "/api/admin/students", tg_id=ADMIN_TG)[1]["students"]]
    assert "Петрова Анна" not in names
    arch = _acall(app, "GET", "/api/admin/students?archived=1", tg_id=ADMIN_TG)[1]["students"]
    assert [x["name"] for x in arch] == ["Петрова Анна"] and arch[0]["archived"]
    card = _acall(app, "GET", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[1]
    assert card["archived"] and card["groups"] == [] and card["partner"] is None and card["parents"] == []
    assert {g["id"]: g["leftPeriod"] for g in card["left"]} == {"GRP-0001": CUR, "GRP-0002": CUR}
    # статистика «ушли» за этот месяц
    flow = _acall(app, "GET", f"/api/admin/flow?ym={CUR}", tg_id=ADMIN_TG)[1]
    assert [x["name"] for x in flow["gone"]] == ["Петрова Анна"] and flow["counts"]["deleted"] == 0
    # родитель больше не привязан
    assert asyncio.run(dp["student_repo"].get_by_parent(("tg", 555))) == []
    # вернуть из архива
    assert _acall(app, "POST", "/api/admin/students/STU-0002/unarchive", tg_id=ADMIN_TG)[0] == 200
    assert _acall(app, "GET", "/api/admin/students/STU-0002", tg_id=ADMIN_TG)[1]["archived"] == ""
    assert _acall(app, "POST", "/api/admin/students/STU-0002/unarchive", tg_id=ADMIN_TG)[0] == 409
    assert _acall(app, "GET", "/api/admin/students/STU-0404/archive", tg_id=ADMIN_TG)[0] == 404


def test_teacher_archive_api_notifies_admins_and_other_teachers(api):  # noqa: F811
    app, dp = api
    _prepare(dp)
    st, p = _call(app, "GET", "/api/teacher/students/STU-0002/archive")
    assert st == 200 and [g["id"] for g in p["groups"]] == ["GRP-0001"] and p["others"] == 1   # чужая группа — числом
    assert _call(app, "POST", "/api/teacher/students/STU-0002/archive", json={})[0] == 409          # долг
    bot = FakeBot()
    st, r = _call(app, "POST", "/api/teacher/students/STU-0002/archive", bot=bot, json={"confirmDebt": True})
    assert st == 200 and [g["id"] for g in r["groups"]] == ["GRP-0001", "GRP-0002"]     # снят и с чужой группы
    assert _student(dp, "STU-0002").archived and all(x.left_period == CUR for x in _rows(dp, "STU-0002").values())
    text = bot.sent[0][1]
    assert "ушёл из школы" in text and "Петрова Анна" in text and "Неоплачено 6800 ₽" in text
    assert "партнёр Иванов Иван отвязан" in text and ADMIN_TG in {x[0] for x in bot.sent}
    # ученик педагогу больше не виден
    assert _call(app, "GET", "/api/teacher/students/STU-0002/archive")[0] == 404
    assert _call(app, "GET", "/api/teacher/students/STU-0002")[0] == 404
    # чужой ученик (не в своих группах) — 404
    asyncio.run(dp["student_group_repo"].add("STU-0001", "GRP-0002"))
    asyncio.run(dp["student_group_repo"].remove("STU-0001", "GRP-0001"))
    assert _call(app, "GET", "/api/teacher/students/STU-0001/archive")[0] == 404


def test_archived_student_keeps_name_in_history(api):  # noqa: F811
    """Имя архивного ученика остаётся в журнале занятий и ленте (карты имён читают с include_archived)."""
    app, dp = api
    asyncio.run(dp["student_repo"].set_archived("STU-0001", "2026-10-21"))
    assert "Иванов" in str(_acall(app, "GET", f"/api/admin/lessons?ym={YM}", tg_id=ADMIN_TG)[1])
    assert "Иванов" in str(_call(app, "GET", f"/api/teacher/lessons?ym={YM}")[1])
