"""Характеризующие тесты для расчёта сумм (bot/services/billing_service.py).

Фиксируют ТЕКУЩЕЕ поведение формул зарплаты и счёта — эталон для рефакторинга.
"""
from bot.models import Teacher, Lesson
from bot.models.enums import LessonType
from bot.services.billing_service import calc_earned, build_billing_rows


def _teacher(rate_group=500, rate_for_teacher=800, rate_for_student=900) -> Teacher:
    return Teacher(
        teacher_id="TCH-0001", tg_id=None, name="Тест",
        rate_group=rate_group, rate_for_teacher=rate_for_teacher,
        rate_for_student=rate_for_student,
    )


def _lesson(**over) -> Lesson:
    base = dict(
        lesson_id="LES-000001", teacher_id="TCH-0001", teacher_name="Тест",
        type=LessonType.INDIVIDUAL,
        student_1_id=None, student_1_name=None,
        student_2_id=None, student_2_name=None,
        date="2026-04-23", duration_min=45, earned=0,
        recorded_at="2026-04-23 10:00:00", updated_at="2026-04-23 10:00:00",
        attendees=None, group_id="",
    )
    base.update(over)
    return Lesson(**base)


# ── calc_earned ────────────────────────────────────────────────────────────

def test_calc_earned_group_uses_rate_group():
    t = _teacher(rate_group=500)
    assert calc_earned(LessonType.GROUP, 45, t) == 500
    assert calc_earned(LessonType.GROUP, 60, t) == 667   # round(666.67)
    assert calc_earned(LessonType.GROUP, 90, t) == 1000


def test_calc_earned_revenue_share_group(monkeypatch):
    """REVENUE_SHARE_GROUPS: зарплата = процент от сбора, время не влияет."""
    from config.settings import settings
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    t = _teacher(rate_group=1500)
    two = "STU-0001:60:1800,STU-0002:60:1800"
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0020", two) == 1800
    assert calc_earned(LessonType.GROUP, 120, t, "GRP-0020", two) == 1800
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0020", "STU-0001:60:1800") == 900
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0020", two + ",STU-0003:60:1800") == 2700
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0020", None) == 0  # никто не пришёл
    # чужая группа — обычная формула по ставке
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0001", two) == 2000


def test_calc_earned_salary_duration_override(monkeypatch):
    """SALARY_DURATION_GROUPS: зарплата группы считается из фикс. минут."""
    from config.settings import settings
    monkeypatch.setattr(settings, "salary_duration_groups", "GRP-0022:90,GRP-0021:0")
    t = _teacher(rate_group=1500)
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0022") == 3000   # 90 мин по ставке
    assert calc_earned(LessonType.GROUP, 120, t, "GRP-0022") == 3000  # выбор педагога не влияет
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0021") == 0      # объединённая: в зарплату не идёт
    assert calc_earned(LessonType.GROUP, 60, t, "GRP-0009") == 2000   # прочие группы — как раньше


def test_calc_earned_individual_uses_rate_for_teacher():
    t = _teacher(rate_for_teacher=800)
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 800
    assert calc_earned(LessonType.INDIVIDUAL, 90, t) == 1600


def test_calc_earned_independent_of_student_count():
    # earned не зависит от числа учеников — только ставка и длительность.
    t = _teacher(rate_for_teacher=800)
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 800


# ── build_billing_rows: групповое ──────────────────────────────────────────

def test_group_without_attendees_returns_empty():
    rows = build_billing_rows(_lesson(type=LessonType.GROUP, attendees=None), _teacher())
    assert rows == []


def test_group_bills_only_positive_amounts():
    lesson = _lesson(type=LessonType.GROUP, duration_min=60,
                     attendees="STU-1:60:700,STU-2:60:0,STU-3:60:700")
    rows = build_billing_rows(lesson, _teacher())
    assert [(r.student_id, r.amount, r.duration_min) for r in rows] == [
        ("STU-1", 700, 60),
        ("STU-3", 700, 60),
    ]


def test_group_old_format_amounts_zero_not_billed():
    # Старый формат attendees (без сумм) = абонемент → строк нет.
    lesson = _lesson(type=LessonType.GROUP, attendees="STU-1,STU-2")
    assert build_billing_rows(lesson, _teacher()) == []


# ── build_billing_rows: индивидуальное / пара ──────────────────────────────

def test_individual_single_student_gets_full_amount():
    t = _teacher(rate_for_student=900)
    lesson = _lesson(duration_min=45, student_1_id="STU-1", student_1_name="Иванов")
    rows = build_billing_rows(lesson, t)
    assert len(rows) == 1
    assert rows[0].student_id == "STU-1"
    assert rows[0].amount == 900


def test_pair_splits_equally_remainder_to_first():
    # base = round(901*45/45) = 901; per=450, остаток 1 → первому 451.
    t = _teacher(rate_for_student=901)
    lesson = _lesson(
        duration_min=45,
        student_1_id="STU-1", student_1_name="Первый",
        student_2_id="STU-2", student_2_name="Второй",
    )
    rows = build_billing_rows(lesson, t)
    amounts = [(r.student_id, r.amount) for r in rows]
    assert amounts == [("STU-1", 451), ("STU-2", 450)]
    # Сумма долей точно равна полной стоимости.
    assert sum(r.amount for r in rows) == 901


def test_three_students_split_remainder_to_first():
    # base = round(1000*45/45) = 1000; per=333, остаток 1 → первому 334.
    t = _teacher(rate_for_student=1000)
    lesson = _lesson(
        duration_min=45,
        student_1_id="STU-1", student_1_name="A",
        student_2_id="STU-2", student_2_name="B",
        student_3_id="STU-3", student_3_name="C",
    )
    rows = build_billing_rows(lesson, t)
    assert [(r.student_id, r.amount) for r in rows] == [
        ("STU-1", 334), ("STU-2", 333), ("STU-3", 333),
    ]
    assert sum(r.amount for r in rows) == 1000


def test_individual_no_participants_returns_empty():
    assert build_billing_rows(_lesson(), _teacher()) == []


def test_billing_row_period_month_derived_from_date():
    t = _teacher(rate_for_student=900)
    lesson = _lesson(date="2026-04-23", student_1_id="STU-1", student_1_name="X")
    rows = build_billing_rows(lesson, t)
    assert rows[0].period_month == "2026-04"


def test_direct_pay_teacher_individual_not_billed(monkeypatch):
    """DIRECT_PAY_TEACHER_IDS: индивидуальные — ни счёта, ни зарплаты; группы как обычно."""
    from config.settings import settings
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0001")
    t = _teacher(rate_group=500, rate_for_teacher=800, rate_for_student=900)
    solo = _lesson(student_1_id="STU-1", student_1_name="A")
    assert build_billing_rows(solo, t) == []
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 0
    group = _lesson(type=LessonType.GROUP, attendees="STU-1:60:700", group_id="GRP-0001")
    assert [r.amount for r in build_billing_rows(group, t)] == [700]
    assert calc_earned(LessonType.GROUP, 45, t) == 500


def test_rate_history_applies_old_rates_to_past_months():
    """Ставки «по август включительно» — старые; сентябрь и дальше — карточка."""
    from bot.services import rate_history
    rate_history.load([rate_history.RateRow("TCH-0001", "2026-08", 400, 600, 700)])
    t = _teacher(rate_group=500, rate_for_teacher=800, rate_for_student=900)  # новые (карточка)
    old_solo = _lesson(date="2026-08-10", student_1_id="STU-1", student_1_name="A")
    new_solo = _lesson(date="2026-09-10", student_1_id="STU-1", student_1_name="A")
    assert build_billing_rows(old_solo, t)[0].amount == 700
    assert build_billing_rows(new_solo, t)[0].amount == 900
    assert calc_earned(LessonType.INDIVIDUAL, 45, t, period="2026-06") == 600
    assert calc_earned(LessonType.GROUP, 45, t, period="2026-08") == 400
    assert calc_earned(LessonType.INDIVIDUAL, 45, t, period="2026-09") == 800
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 800  # без периода — текущие


def test_rate_history_picks_nearest_boundary():
    """Несколько исторических строк: берётся ближайшая граница ≥ периода."""
    from bot.services import rate_history
    rate_history.load([
        rate_history.RateRow("TCH-0001", "2026-05", 300, 500, 600),
        rate_history.RateRow("TCH-0001", "2026-08", 400, 600, 700),
    ])
    t = _teacher(rate_for_student=900)
    assert rate_history.effective_rates(t, "2026-04")[2] == 600
    assert rate_history.effective_rates(t, "2026-05")[2] == 600
    assert rate_history.effective_rates(t, "2026-06")[2] == 700
    assert rate_history.effective_rates(t, "2026-09")[2] == 900


def test_group_salary_rate_overrides_teacher_card_rate(monkeypatch):
    """GROUP_SALARY_RATES: в «своей» группе ставка педагога другая (БП Джаз — 2000 ₽ за час)."""
    from config.settings import settings
    teacher = _teacher(rate_group=1350)
    monkeypatch.setattr(settings, "group_salary_rates", "GRP-0019:1500")
    assert calc_earned(LessonType.GROUP, 60, teacher, "GRP-0019") == 2000
    assert calc_earned(LessonType.GROUP, 90, teacher, "GRP-0019") == 3000     # пропорционально, как везде
    assert calc_earned(LessonType.GROUP, 45, teacher, "GRP-0001") == 1350     # другая группа — ставка карточки
    assert calc_earned(LessonType.INDIVIDUAL, 45, teacher, "GRP-0019") == teacher.rate_for_teacher


def test_rate_history_supports_mid_month_price_change():
    """Цена выросла 4 сентября: занятия 1–3 считаются по старой, с 4-го — по новой."""
    from bot.services import rate_history
    teacher = _teacher(rate_group=1125, rate_for_teacher=1400, rate_for_student=2200)
    rate_history.load([
        rate_history.RateRow("TCH-0001", "2026-08", 1125, 1300, 1900),       # по конец августа
        rate_history.RateRow("TCH-0001", "2026-09-03", 1125, 1300, 1900),    # и ещё три дня сентября
    ])
    try:
        assert rate_history.effective_rates(teacher, "2026-08-20")[2] == 1900
        assert rate_history.effective_rates(teacher, "2026-09-03")[2] == 1900
        assert rate_history.effective_rates(teacher, "2026-09-04")[2] == 2200
        # индивидуальное занятие: ставка педагога тоже берётся на дату
        assert calc_earned(LessonType.INDIVIDUAL, 45, teacher, period="2026-09-03") == 1300
        assert calc_earned(LessonType.INDIVIDUAL, 45, teacher, period="2026-09-10") == 1400
        # запрос месяцем — ставки на конец месяца (как было)
        assert rate_history.effective_rates(teacher, "2026-09")[2] == 2200
    finally:
        rate_history.load([])


def test_personal_student_rate_replaces_client_rate(monkeypatch):
    """Персональная цена ученика у педагога: счёт по ней, зарплата педагога прежняя."""
    from config.settings import settings
    monkeypatch.setattr(settings, "student_lesson_rates", "TCH-0001:STU-1:3500")
    t = _teacher(rate_group=2500, rate_for_teacher=2500, rate_for_student=3000)

    mine = _lesson(student_1_id="STU-1", student_1_name="Прудникова Дарья")
    other = _lesson(student_1_id="STU-2", student_1_name="Другой Ученик")
    assert [r.amount for r in build_billing_rows(mine, t)] == [3500]
    assert [r.amount for r in build_billing_rows(other, t)] == [3000]      # остальным прежняя цена
    assert calc_earned(LessonType.INDIVIDUAL, 45, t) == 2500               # зарплата не меняется

    hour = _lesson(duration_min=60, student_1_id="STU-1", student_1_name="Прудникова Дарья")
    assert [r.amount for r in build_billing_rows(hour, t)] == [4667]       # ставка за 45 мин


def test_personal_rate_starts_from_its_month(monkeypatch):
    """Месяц в настройке защищает уже оплаченные периоды от пересчёта."""
    from config.settings import settings
    monkeypatch.setattr(settings, "student_lesson_rates", "TCH-0001:STU-1:3500:2026-09")
    t = _teacher(rate_group=2500, rate_for_teacher=2500, rate_for_student=3000)
    before = _lesson(date="2026-05-01", student_1_id="STU-1", student_1_name="Прудникова Дарья")
    after = _lesson(date="2026-09-01", student_1_id="STU-1", student_1_name="Прудникова Дарья")
    assert [r.amount for r in build_billing_rows(before, t)] == [3000]
    assert [r.amount for r in build_billing_rows(after, t)] == [3500]


def test_personal_rate_in_a_pair_charges_each_their_own_half(monkeypatch):
    """В паре каждый платит половину своей цены."""
    from config.settings import settings
    monkeypatch.setattr(settings, "student_lesson_rates", "TCH-0001:STU-1:3500")
    t = _teacher(rate_group=2500, rate_for_teacher=2500, rate_for_student=3000)
    pair = _lesson(student_1_id="STU-1", student_1_name="Прудникова Дарья",
                   student_2_id="STU-2", student_2_name="Другой Ученик")
    assert [r.amount for r in build_billing_rows(pair, t)] == [1750, 1500]
