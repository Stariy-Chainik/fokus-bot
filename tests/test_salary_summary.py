"""Сводка зарплаты «по ставкам» (bot/services/salary_summary.py): строки SalaryService → одна строка на правило."""
from bot.models import Lesson, Teacher
from bot.models.enums import LessonType
from bot.services.billing_service import calc_earned, salary_rule
from bot.services.salary_service import SalaryLine, compute_salary_lines
from bot.services.salary_summary import rate_text, summarize, summary_dicts
from config.settings import settings

GROUPS = {"GRP-0021": "ВБ ХГ Младшая", "GRP-0022": "ВБ ХГ Средняя", "GRP-0023": "ВБ ХГ Старшая",
          "GRP-0008": "ЮБ сад ХГ 16:00", "GRP-0028": "ЮБ сад ХГ 17:00", "GRP-0020": "ХГ Индивидуальные"}
NAMES = {"STU-1": "Иванова Аня", "STU-2": "Петрова Оля"}


def _teacher():
    return Teacher("TCH-0011", None, "Яковлева", 1275, 1000, 1800)


def _lesson(lid, date, gid, dur=60, typ=LessonType.GROUP, att=None, merged="", students=()):
    s = list(students) + [(None, None)] * 2
    return Lesson(lesson_id=lid, teacher_id="TCH-0011", teacher_name="Яковлева", type=typ,
                  student_1_id=s[0][0], student_1_name=s[0][1], student_2_id=s[1][0], student_2_name=s[1][1],
                  date=date, duration_min=dur, earned=0, recorded_at="", updated_at="",
                  attendees=att, group_id=gid, merged_groups=merged)


def _summary(lessons, t=None, **kw):
    t = t or _teacher()
    lines = compute_salary_lines(t, lessons, shift_label="Смена Боброво")
    return summarize(lines, {ls.lesson_id: ls for ls in lessons}, t, GROUPS, NAMES, **kw)


def test_rate_text_per_hour_when_divisible():
    assert rate_text(1050) == "1 400 ₽/ч" and rate_text(1275) == "1 700 ₽/ч"
    assert rate_text(1000) == "1 000 ₽ за 45 мин"


def test_salary_rule_matches_calc_earned(monkeypatch):
    monkeypatch.setattr(settings, "group_salary_rates", "GRP-0031:1050:2026-10")
    monkeypatch.setattr(settings, "salary_duration_groups", "GRP-0025:45")
    t = _teacher()
    for args in ((LessonType.GROUP, 60, t, "GRP-0031", None, "2026-10-13"),
                 (LessonType.GROUP, 60, t, "GRP-0025", None, "2026-10-01"),
                 (LessonType.INDIVIDUAL, 90, t, "", None, "2026-10-01"),
                 (LessonType.GROUP, 60, t, "GRP-0008", None, "2026-10-02")):
        assert salary_rule(*args).amount == calc_earned(*args)
    r = salary_rule(LessonType.GROUP, 60, t, "GRP-0031", None, "2026-10-13")
    assert (r.kind, r.rate, r.minutes, r.group_rate, r.fixed) == ("rate", 1050, 60, True, False)
    r = salary_rule(LessonType.GROUP, 90, t, "GRP-0025", None, "2026-10-01")
    assert (r.rate, r.minutes, r.fixed, r.amount) == (1275, 45, True, 1275)


def test_shift_groups_fold_into_one_row(monkeypatch):
    """Боброво: три группы в день — одна строка смены с днями, а не 42 нулевые строки «в смене»."""
    monkeypatch.setattr(settings, "shift_groups", "GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180")
    monkeypatch.setattr(settings, "group_salary_rates", "GRP-0021:1050:2026-10,GRP-0022:1050:2026-10,GRP-0023:1050:2026-10")
    lessons = [_lesson("L1", "2026-10-01", "GRP-0021"), _lesson("L2", "2026-10-01", "GRP-0022", 120),
               _lesson("L3", "2026-10-01", "GRP-0023", 120), _lesson("L4", "2026-10-04", "GRP-0022", 120)]
    rows = _summary(lessons)
    assert [r.kind for r in rows] == ["shift"]
    r = rows[0]
    assert (r.title, r.count, r.minutes, r.amount) == ("Смена Боброво", 2, 300, 4200 + 2800)
    assert r.rule.startswith("1 400 ₽/ч за время смены")
    assert [(i.date, i.label, i.amount) for i in r.items] == [
        ("2026-10-01", "3 ч · ВБ ХГ Младшая 60 мин · ВБ ХГ Средняя 120 мин · ВБ ХГ Старшая 120 мин", 4200),
        ("2026-10-04", "2 ч · ВБ ХГ Средняя 120 мин", 2800)]


def test_group_rows_with_free_and_merged_lessons(monkeypatch):
    """Сад: 1700 ₽ за занятие, одно без оплаты (ставка 0 по истории), одно объединённое с 17:00 — как одно."""
    from bot.services import rate_history
    rate_history.load([rate_history.RateRow("TCH-0011", "2026-10-19", 1275, 1000, 1800),
                       rate_history.RateRow("TCH-0011", "2026-10-20", 0, 1000, 1800)])
    try:
        lessons = [_lesson("L1", "2026-10-02", "GRP-0008", att="STU-1:60:850"),
                   _lesson("L2", "2026-10-09", "GRP-0008", att="STU-1:60:850", merged="GRP-0028"),
                   _lesson("L3", "2026-10-20", "GRP-0008", att="STU-1:60:850"),
                   _lesson("L4", "2026-10-23", "GRP-0028", att="STU-2:60:850")]
        rows = {r.key: r for r in _summary(lessons)}
    finally:
        rate_history.load([])
    g8 = rows["g:GRP-0008"]
    assert (g8.title, g8.count, g8.free, g8.merged, g8.amount) == ("ЮБ сад ХГ 16:00", 3, 1, 1, 3400)
    assert g8.rule == "ставка педагога 1 700 ₽/ч"
    assert [(i.label, i.amount, i.note) for i in g8.items] == [
        ("ЮБ сад ХГ 16:00", 1700, ""), ("ЮБ сад ХГ 16:00 + 17:00", 1700, "две группы одним занятием"),
        ("ЮБ сад ХГ 16:00", 0, "без оплаты")]
    assert rows["g:GRP-0028"].amount == 1700


def test_revenue_share_individual_direct_and_fixed_duration(monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    monkeypatch.setattr(settings, "salary_duration_groups", "GRP-0025:45")
    monkeypatch.setattr(settings, "group_salary_rates", "GRP-0025:1000")
    lessons = [_lesson("L1", "2026-10-02", "GRP-0020", att="STU-1:60:1800,STU-2:60:1800"),
               _lesson("L2", "2026-10-10", "GRP-0020", att="STU-1:60:900"),
               _lesson("L3", "2026-10-03", "", 90, LessonType.INDIVIDUAL, students=[("STU-1", "Иванова Аня")]),
               _lesson("L4", "2026-10-04", "GRP-0025", 90)]
    rows = {r.key: r for r in _summary(lessons)}
    rs = rows["rshare:GRP-0020"]
    assert (rs.kind, rs.title, rs.count, rs.amount) == ("revenue_share", "ХГ Индивидуальные", 2, 2250)
    assert rs.rule == "50 % от сбора с учениц · сбор 4 500 ₽"
    assert [(i.label, i.note) for i in rs.items] == [("Иванова Аня + Петрова Оля", "сбор 3 600 ₽"), ("Иванова Аня", "сбор 900 ₽")]
    ind = rows["ind"]
    assert (ind.title, ind.amount, ind.rule) == ("Индивидуальные и парные", 2000, "ставка педагога 1 000 ₽ за 45 мин = 2 000 ₽ за 90 мин")
    g25 = rows["g:GRP-0025"]
    assert g25.amount == 1000
    assert g25.rule == "1 000 ₽ за занятие любой длительности · ставка группы 1 000 ₽ за 45 мин"
    # прямая оплата: школа не начисляет — строка с нулём в конце
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0011")
    rows = _summary(lessons[2:3])
    assert [(r.kind, r.amount, r.count) for r in rows] == [("direct", 0, 1)]


def test_share_bonus_override_rows_and_json(monkeypatch):
    monkeypatch.setattr(settings, "subscription_share_groups", "GRP-0021:30:2026-10")
    t = _teacher()
    lessons = [_lesson("L1", "2026-10-01", "GRP-0021"), _lesson("L2", "2026-10-08", "GRP-0021")]
    lines = compute_salary_lines(t, lessons, overrides={"2026-10-15": (90, "замена")}) + [
        SalaryLine("2026-10-01", "share", "30 % от абонементов: ВБ ХГ Младшая — 53 000 ₽", 0, 15900, group_id="GRP-0021"),
        SalaryLine("2026-10-20", "bonus", "Премия: турнир", 0, 5000)]
    by_id = {ls.lesson_id: ls for ls in lessons}
    rows = {r.key: r for r in summarize(lines, by_id, t, GROUPS, NAMES)}
    assert rows["share:GRP-0021"].rule == "30 % от абонементов: ВБ ХГ Младшая — 53 000 ₽"
    assert (rows["share:GRP-0021"].count, rows["share:GRP-0021"].amount) == (2, 15900)
    assert summarize(lines, by_id, t, GROUPS, NAMES, for_teacher=True)[0].rule == "процент от абонементов группы за месяц"
    assert (rows["bonus"].amount, rows["bonus"].items[0].label) == (5000, "турнир")
    assert (rows["override"].amount, rows["override"].items[0].label) == (2550, "1 ч 30 мин · замена")
    d = summary_dicts([rows["bonus"]])[0]
    assert set(d) == {"key", "kind", "title", "rule", "count", "minutes", "amount", "groupId", "free", "merged", "items"}
    assert d["items"][0] == {"date": "2026-10-20", "label": "турнир", "minutes": 0, "amount": 5000, "lessonId": None, "note": ""}
