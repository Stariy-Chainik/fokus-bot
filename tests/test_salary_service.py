"""Смены (SHIFT_GROUPS) и корректировки дней — bot/services/salary_service.py."""
from bot.models import Teacher, Lesson
from bot.models.enums import LessonType
from bot.services.salary_service import (
    parse_shift_groups, shift_minutes, compute_salary_lines, salary_total,
)
from config.settings import settings

SHIFT = {"GRP-0021": (0, 60), "GRP-0022": (0, 120), "GRP-0023": (60, 180)}


def _teacher():
    return Teacher("TCH-0011", None, "Яковлева", 1275, 1000, 1800)


def _lesson(lid, date, gid, dur=60, typ=LessonType.GROUP, att=None):
    return Lesson(lesson_id=lid, teacher_id="TCH-0011", teacher_name="Яковлева", type=typ,
                  student_1_id=None, student_1_name=None, student_2_id=None, student_2_name=None,
                  date=date, duration_min=dur, earned=0, recorded_at="", updated_at="",
                  attendees=att, group_id=gid)


def test_parse_and_union():
    assert parse_shift_groups("GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180") == SHIFT
    assert shift_minutes({"GRP-0021", "GRP-0022", "GRP-0023"}, SHIFT) == 180
    assert shift_minutes({"GRP-0022"}, SHIFT) == 120
    assert shift_minutes({"GRP-0021", "GRP-0022"}, SHIFT) == 120
    assert shift_minutes({"GRP-0021"}, SHIFT) == 60
    assert shift_minutes({"GRP-0022", "GRP-0023"}, SHIFT) == 180
    assert shift_minutes({"GRP-0021", "GRP-0023"}, SHIFT) == 180  # 0-60 + 60-180 стыкуются
    assert shift_minutes(set(), SHIFT) == 0


def test_shift_day_salary(monkeypatch):
    monkeypatch.setattr(settings, "shift_groups", "GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180")
    t = _teacher()
    lessons = [
        _lesson("L1", "2026-09-03", "GRP-0021", 60), _lesson("L2", "2026-09-03", "GRP-0022", 120),
        _lesson("L3", "2026-09-03", "GRP-0023", 120),
        _lesson("L4", "2026-09-05", "GRP-0022", 120),                       # только Средняя — 2 ч
        _lesson("L5", "2026-09-08", "GRP-0008", 60, att="STU-1:60:850"),    # обычная группа
    ]
    lines = compute_salary_lines(t, lessons, shift_label="Боброво")
    by_kind = {}
    for ln in lines:
        by_kind.setdefault(ln.kind, []).append(ln)
    assert all(ln.amount == 0 for ln in by_kind["in_shift"])
    shifts = {ln.date: ln.amount for ln in by_kind["shift"]}
    assert shifts == {"2026-09-03": 5100, "2026-09-05": 3400}   # 3 ч и 2 ч × 1700
    assert by_kind["lesson"][0].amount == 1700                     # 1275 × 60/45
    assert salary_total(lines) == 5100 + 3400 + 1700


def test_override_replaces_shift(monkeypatch):
    monkeypatch.setattr(settings, "shift_groups", "GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180")
    t = _teacher()
    lessons = [_lesson("L1", "2026-09-03", "GRP-0022", 120), _lesson("L2", "2026-09-03", "GRP-0023", 120)]
    lines = compute_salary_lines(t, lessons, overrides={"2026-09-03": (90, "ушла раньше"),
                                                        "2026-09-10": (60, "замена")})
    ov = {ln.date: (ln.amount, ln.label) for ln in lines if ln.kind == "override"}
    assert ov["2026-09-03"][0] == 2550 and "ушла раньше" in ov["2026-09-03"][1]
    assert ov["2026-09-10"][0] == 1700          # день без занятий, но с корректировкой
    assert not [ln for ln in lines if ln.kind == "shift"]  # расчёт по группам заменён
