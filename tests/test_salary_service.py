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


def test_override_zeroes_all_lessons_of_the_day(monkeypatch):
    """Корректировка заменяет всю оплату дня (решение владельца 21.10.2026: сад 20.10 без оплаты),
    а не только смену: обычное занятие в этот день — 0 ₽ строкой in_override."""
    monkeypatch.setattr(settings, "shift_groups", "")
    t = _teacher()
    lessons = [_lesson("L1", "2026-10-20", "GRP-0008", 60), _lesson("L2", "2026-10-21", "GRP-0008", 60)]
    lines = compute_salary_lines(t, lessons, overrides={"2026-10-20": (0, "сад без оплаты")})
    by = {(ln.date, ln.kind): ln for ln in lines}
    assert by[("2026-10-20", "in_override")].amount == 0 and by[("2026-10-20", "in_override")].lesson_id == "L1"
    assert by[("2026-10-20", "override")].amount == 0 and "сад без оплаты" in by[("2026-10-20", "override")].label
    assert by[("2026-10-21", "lesson")].amount == 1700                 # соседний день не тронут
    assert salary_total(lines) == 1700


def test_shift_rate_from_group_salary_rates_since_month(monkeypatch):
    """Боброво у Яковлевой — 1400 ₽/ч с октября 2026 (решение владельца 12.10.2026): ставка часа смены
    из GROUP_SALARY_RATES с месяцем; сентябрь — прежние 1700 ₽/ч, сад — по ставке педагога."""
    monkeypatch.setattr(settings, "shift_groups", "GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180")
    monkeypatch.setattr(settings, "group_salary_rates",
                        "GRP-0019:1500,GRP-0021:1050:2026-10,GRP-0022:1050:2026-10,GRP-0023:1050:2026-10")
    assert settings.group_salary_rate("GRP-0021", "2026-09-30") is None
    assert settings.group_salary_rate("GRP-0021", "2026-10-01") == 1050
    assert settings.group_salary_rate("GRP-0019", "2026-01") == 1500           # без месяца — всегда
    t = _teacher()
    day = ["GRP-0021", "GRP-0022", "GRP-0023"]
    lessons = [_lesson(f"S{i}", "2026-09-07", g) for i, g in enumerate(day)] + \
              [_lesson(f"O{i}", "2026-10-05", g) for i, g in enumerate(day)] + \
              [_lesson("O9", "2026-10-06", "GRP-0008", 60, att="STU-1:60:850")]
    shifts = {ln.date: ln.amount for ln in compute_salary_lines(t, lessons) if ln.kind == "shift"}
    assert shifts == {"2026-09-07": 5100, "2026-10-05": 4200}                  # 3 ч × 1700 → 3 ч × 1400
    assert next(ln for ln in compute_salary_lines(t, lessons) if ln.lesson_id == "O9").amount == 1700


def test_group_salary_rate_rows_pick_latest_started(monkeypatch):
    monkeypatch.setattr(settings, "group_salary_rates", "GRP-1:1000,GRP-1:1200:2026-10,GRP-1:1300:2027-01")
    assert [settings.group_salary_rate("GRP-1", p) for p in ("2026-09", "2026-12-31", "2027-02")] == [1000, 1200, 1300]
    assert settings.group_salary_rate("GRP-1") == 1300
