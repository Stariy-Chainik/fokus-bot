"""SalaryService: обычные занятия, фильтр по дню, корректировки из репозитория."""
from bot.models.enums import LessonType
from bot.repositories.teacher_bonus_repo import TeacherBonus
from bot.services.salary_service import SalaryService, compute_salary_lines, salary_total
from config.settings import settings
from tests.fakes import BonusRepoFake, LessonRepoFake, OverrideRepoFake, mk_lesson, mk_teacher, run


def _lessons(t):
    return [
        mk_lesson("L1", t, "2026-09-01", 45, students=[("STU-1", "A")]),                  # 1500
        mk_lesson("L2", t, "2026-09-01", 60, LessonType.GROUP, group_id="GRP-1"),          # 1333
        mk_lesson("L3", t, "2026-09-03", 90, students=[("STU-1", "A"), ("STU-2", "B")]),  # 3000
    ]


def test_compute_salary_lines_plain_lessons():
    t = mk_teacher(rate_group=1000, rate_for_teacher=1500)
    lines = compute_salary_lines(t, _lessons(t))
    assert [(ln.kind, ln.date, ln.minutes, ln.amount, ln.lesson_id) for ln in lines] == [
        ("lesson", "2026-09-01", 45, 1500, "L1"),
        ("lesson", "2026-09-01", 60, 1333, "L2"),
        ("lesson", "2026-09-03", 90, 3000, "L3"),
    ]
    assert salary_total(lines) == 5833


def test_direct_pay_individual_gives_zero_salary(monkeypatch):
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    t = mk_teacher(rate_group=1000, rate_for_teacher=1500)
    lines = compute_salary_lines(t, _lessons(t))
    assert [ln.amount for ln in lines] == [0, 1333, 0]


def test_lines_for_month_and_day_and_total():
    t = mk_teacher(rate_group=1000, rate_for_teacher=1500)
    svc = SalaryService(LessonRepoFake(_lessons(t)), OverrideRepoFake([("TCH-0001", "2026-09-10", 90, "замена")]))
    month = run(svc.lines_for(t, "2026-09"))
    assert [ln.kind for ln in month] == ["lesson", "lesson", "lesson", "override"]
    assert month[-1].amount == 2000 and month[-1].label.startswith("Корректировка: замена — 1 ч 30 мин")
    day = run(svc.lines_for(t, "2026-09-01"))
    assert [ln.lesson_id for ln in day] == ["L1", "L2"]
    assert run(svc.total_for(t, "2026-09")) == 5833 + 2000
    assert run(svc.total_for(t, "2026-09-03")) == 3000


def test_overrides_for_without_repo_is_empty():
    svc = SalaryService(LessonRepoFake([]), None)
    assert run(svc.overrides_for("TCH-0001", "2026-09")) == {}


def test_bonus_is_a_month_line_after_lessons_not_in_day():
    """Премия (teacher_bonuses): строка «Премия: …» после занятий, в итоге месяца; в день не попадает."""
    t = mk_teacher(rate_group=1000, rate_for_teacher=1500)
    bonus = TeacherBonus("BN-000001", "TCH-0001", "2026-09", 5000, "турнир", "2026-10-02 10:00:00", 1, "PO-000001")
    svc = SalaryService(LessonRepoFake(_lessons(t)), None, BonusRepoFake([bonus]))
    month = run(svc.lines_for(t, "2026-09"))
    assert [ln.kind for ln in month] == ["lesson", "lesson", "lesson", "bonus"]
    assert (month[-1].date, month[-1].label, month[-1].minutes, month[-1].amount) == ("2026-10-02", "Премия: турнир", 0, 5000)
    assert run(svc.total_for(t, "2026-09")) == 5833 + 5000
    assert [ln.kind for ln in run(svc.lines_for(t, "2026-09-01"))] == ["lesson", "lesson"]
    assert run(svc.lines_for(t, "2026-10-02")) == [] and run(svc.lines_for(t, "2026-10")) == []
    assert run(SalaryService(LessonRepoFake([]), None).bonus_lines("TCH-0001", "2026-09")) == []
