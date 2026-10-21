"""Корректировка дня видна везде, где показана зарплата за занятие (21.10.2026, сад Яковлевой 20.10 без оплаты):
журнал и карточка занятия педагога, список и карточка занятия у администратора, «Сегодня» на сводке и «Прибыль»
берут сумму из строк SalaryService, а не считают по ставке заново."""
import asyncio

from bot.services.profit_service import calculate_profit_lesson, calculate_teacher_profit
from bot.services.salary_service import lesson_amounts
from tests.test_admin_api import ADMIN_TG, GROUP_DAY, _call, make_api
from tests.test_teacher_api import TEACHER_TG
from tests.test_teacher_api import _call as _tcall


def _with_override(dp, day):
    """Корректировка 0 минут на день группового занятия Реки (LES-3, 60 мин, 1333 ₽ по ставке)."""
    svc = dp["salary_service"]
    svc._override_repo = dp["salary_override_repo"]
    asyncio.run(dp["salary_override_repo"].add("TCH-0001", day, 0, "без оплаты", ADMIN_TG))
    return svc


def test_admin_screens_show_zero_for_override_day(monkeypatch):
    dp, _ = make_api(monkeypatch)
    _with_override(dp, GROUP_DAY)
    d = _call(dp, "GET", f"/api/admin/lessons?date={GROUP_DAY}", tg_id=ADMIN_TG)[1]
    assert [x["earned"] for x in d["lessons"]] == [0] and d["earned"] == 0
    one = _call(dp, "GET", "/api/admin/lessons/LES-3", tg_id=ADMIN_TG)[1]
    assert one["earned"] == 0 and one["economy"]["salary"] == 0
    # соседние дни считаются по ставке, как раньше
    assert _call(dp, "GET", "/api/admin/lessons/LES-1", tg_id=ADMIN_TG)[1]["earned"] == 1500


def test_teacher_journal_shows_zero_for_override_day(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(TEACHER_TG, teacher_id="TCH-0001"))
    _with_override(dp, GROUP_DAY)
    d = _tcall(dp, "GET", f"/api/teacher/lessons?date={GROUP_DAY}", tg_id=TEACHER_TG)[1]
    assert [x["earned"] for x in d["lessons"]] == [0]
    assert _tcall(dp, "GET", "/api/teacher/lessons/LES-3", tg_id=TEACHER_TG)[1]["earned"] == 0
    assert _tcall(dp, "GET", "/api/teacher/lessons/LES-1", tg_id=TEACHER_TG)[1]["earned"] == 1500


def test_profit_uses_salary_lines_not_rate(monkeypatch):
    dp, _ = make_api(monkeypatch)
    svc = _with_override(dp, GROUP_DAY)
    teacher = asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))
    lessons = asyncio.run(dp["lesson_repo"].get_by_teacher_and_period("TCH-0001", GROUP_DAY[:7]))
    lines = asyncio.run(svc.lines_for(teacher, GROUP_DAY[:7]))
    amounts = lesson_amounts(lines)
    assert amounts["LES-3"] == 0 and amounts["LES-1"] == 1500
    plain = calculate_teacher_profit(teacher, lessons)                          # по ставке: 1333 входит
    fixed = calculate_teacher_profit(teacher, lessons, lesson_salary=amounts)   # по строкам: занятие дня — 0
    assert plain is not None and fixed is not None and plain.salary - fixed.salary == 1333
    assert calculate_profit_lesson(next(x for x in lessons if x.lesson_id == "LES-3"), teacher, earned=0).salary == 0
