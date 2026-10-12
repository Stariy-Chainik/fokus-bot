"""Зарплата педагога — процент от начисленных абонементов группы (SUBSCRIPTION_SHARE_GROUPS; решение владельца
11.10.2026: Яковлева в Боброво, три группы ВБ ХГ — 30 % с октября 2026, раньше — смена по часам)."""
import asyncio

import pytest

from bot.models import StudentGroup
from bot.models.enums import GroupBillingMode, LessonType
from bot.services.billing_service import calc_earned
from bot.services.salary_service import SalaryService, compute_salary_lines
from config.settings import settings
from tests.fakes import LessonRepoFake, mk_group, mk_lesson, mk_student, mk_teacher
from tests.test_admin_api import _call, make_api

YAK = mk_teacher("TCH-0011", "Яковлева Олеся", rate_group=1275)
SHARE = "GRP-0021:30:2026-10,GRP-0022:30:2026-10"


def _grp(lid, day, gid, teacher=YAK):
    return mk_lesson(lid, teacher, day, duration=60, lesson_type=LessonType.GROUP, group_id=gid)


@pytest.fixture()
def share(monkeypatch):
    monkeypatch.setattr(settings, "subscription_share_groups", SHARE)
    monkeypatch.setattr(settings, "shift_groups", "GRP-0021:0-60,GRP-0022:0-120")


def test_settings_by_month(share):
    assert settings.subscription_share_map["GRP-0021"] == (30, "2026-10")
    assert settings.subscription_share("GRP-0021", "2026-10-05") == 30
    assert settings.subscription_share("GRP-0021", "2026-09-30") is None        # сентябрь — по-старому
    assert settings.subscription_share("GRP-0099", "2026-10") is None


def test_lessons_pay_nothing_and_no_shift_from_october(share):
    assert calc_earned(LessonType.GROUP, 60, YAK, "GRP-0021", None, "2026-10-05") == 0
    sept = compute_salary_lines(YAK, [_grp("L1", "2026-09-07", "GRP-0021"), _grp("L2", "2026-09-07", "GRP-0022")])
    assert [x.kind for x in sept] == ["in_shift", "in_shift", "shift"] and sept[-1].amount == 3400   # 2 ч × 1700
    octo = compute_salary_lines(YAK, [_grp("L3", "2026-10-05", "GRP-0021"), _grp("L4", "2026-10-05", "GRP-0022")])
    assert [(x.kind, x.amount) for x in octo] == [("in_share", 0), ("in_share", 0)]


def test_share_line_is_percent_of_accrued_subscriptions(share):
    other = mk_teacher("TCH-0002", "Замена")
    lessons = [_grp("L1", "2026-10-01", "GRP-0021"), _grp("L2", "2026-10-03", "GRP-0021"),
               _grp("L3", "2026-10-05", "GRP-0021", other),                       # одно занятие провела замена
               _grp("L4", "2026-10-01", "GRP-0022"),
               mk_lesson("L5", YAK, "2026-10-02", duration=60, lesson_type=LessonType.GROUP, group_id="GRP-0008")]

    async def revenue(month):
        assert month == "2026-10"
        return [("GRP-0021", "ВБ ХГ Младшая", 9, 54000), ("GRP-0022", "ВБ ХГ Средняя", 4, 28000)]

    svc = SalaryService(LessonRepoFake(lessons), subscription_revenue=revenue)
    lines = asyncio.run(svc.lines_for(YAK, "2026-10"))
    share_lines = {x.group_id: x for x in lines if x.kind == "share"}
    assert share_lines["GRP-0021"].amount == 10800                      # 30 % × 54 000 × 2/3 занятий
    assert "ваши 2 из 3 занятий" in share_lines["GRP-0021"].label
    assert share_lines["GRP-0022"].amount == 8400 and share_lines["GRP-0022"].label == \
        "30 % от абонементов: ВБ ХГ Средняя — 28 000 ₽"
    assert next(x for x in lines if x.lesson_id == "L5").amount == 1700    # сад — по-прежнему по ставке
    assert [x for x in asyncio.run(svc.lines_for(YAK, "2026-10-01")) if x.kind == "share"] == []   # не в итоге дня
    sub = asyncio.run(svc.lines_for(other, "2026-10"))
    assert [x.amount for x in sub if x.kind == "share"] == [5400]           # замене — 1/3


def test_admin_salaries_and_profit_use_share(monkeypatch):
    app, dp = make_api(monkeypatch)
    teacher = dp["teacher_repo"].items[0]
    dp["group_repo"].items.append(mk_group("GRP-0021", "ВБ ХГ Младшая", billing_mode=GroupBillingMode.SUBSCRIPTION,
                                           price_full=6000))
    dp["student_repo"].items.append(mk_student("STU-0003", "Сидорова Аня"))
    dp["student_group_repo"].rows += [StudentGroup("STU-0003", "GRP-0021"), StudentGroup("STU-0002", "GRP-0021")]
    ym = "2026-12"                       # месяц без других занятий из make_api
    dp["lesson_repo"].items.append(_grp("LES-50", f"{ym}-01", "GRP-0021", teacher))
    monkeypatch.setattr(settings, "subscription_share_groups", "GRP-0021:30:2026-10")
    rows = {t["id"]: t for t in _call(app, "GET", f"/api/admin/salaries?ym={ym}")[1]["teachers"]}
    assert rows["TCH-0001"]["accrued"] == 3600 and rows["TCH-0001"]["lessons"] == 1     # 30 % × 2 × 6000
    profit = _call(app, "GET", f"/api/admin/profit?ym={ym}")[1]["totals"]
    assert profit["salary"] == 3600


def test_teacher_sees_only_group_and_own_amount(monkeypatch):
    """Педагогу не показываем, сколько начислено группе (решение владельца 12.10.2026)."""
    from tests.test_teacher_api import TEACHER_TG, _call as t_call
    app, dp = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    teacher = dp["teacher_repo"].items[0]
    dp["group_repo"].items.append(mk_group("GRP-0021", "ВБ ХГ Младшая", billing_mode=GroupBillingMode.SUBSCRIPTION,
                                           price_full=6000))
    dp["student_group_repo"].rows.append(StudentGroup("STU-0002", "GRP-0021"))
    dp["teacher_group_repo"]._t2g[teacher.teacher_id].append("GRP-0021")
    dp["lesson_repo"].items.append(_grp("LES-50", "2026-12-01", "GRP-0021", teacher))
    monkeypatch.setattr(settings, "subscription_share_groups", "GRP-0021:30:2026-10")
    status, st = t_call(app, "GET", "/api/teacher/stats?ym=2026-12")
    share = [x for x in st["lines"] if x["kind"] == "share"]
    assert status == 200 and share == [{**share[0], "label": "ВБ ХГ Младшая", "amount": 1800}]
    assert "6 000" not in str(st) and "6000" not in str(st)
