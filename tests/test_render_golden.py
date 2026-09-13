"""Golden-снимки экранов хендлеров: карточки, занятия, должники, история оплат, выплаты.

Фиксируют текущий текст и кнопки байт-в-байт перед рефакторингом хендлеров.
"""
from bot.models import StudentGroup
from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services import PaymentService, StudentService, TeacherVisibilityService
from bot.services.salary_service import SalaryService
from config.settings import settings
from tests.fakes import (
    SHORT, ByIdRepo, ClientRepoFake, FakeCallbackQuery, FakeMessage, FakeState, LessonRepoFake, PaymentRepoFake,
    StudentGroupRepoFake, StudentRepoFake, SubmissionRepoFake, TeacherGroupRepoFake, assert_golden,
    install_fake_show_card, mk_branch, mk_client, mk_group, mk_lesson, mk_payment, mk_student, mk_submission,
    mk_teacher, mk_user, run, screen_dump,
)

# ─── Общий мир ────────────────────────────────────────────────────────────────

T1 = mk_teacher("TCH-0001", "Река Станислав", 1000, 1500, 2000)
T2 = mk_teacher("TCH-0002", "Клецова Ангелина", 1350, 1500, 2000)
T3 = mk_teacher("TCH-0003", "Никишин Влад", 2500, 2500, 3000)


def _world():
    students = [
        mk_student("STU-0001", "Иванов Иван", partner_id="STU-0002", client_id="CLT-0001",
                   group_tier=SHORT, athlete_tg_id=555, parent_tg_ids=[100]),
        mk_student("STU-0002", "Петрова Анна", partner_id="STU-0001", parent_tg_ids=[100, 101]),
        mk_student("STU-0003", "Сидоров Пётр"),
    ]
    groups = [
        mk_group("GRP-0001", "ЮБ сад БТ 👯", "BRN-0001", GroupBillingMode.PER_VISIT,
                 price_full=850, price_short=600, duration_short=35, duration_full=60),
        mk_group("GRP-0002", "БП БТ Спортивная", "BRN-0002", GroupBillingMode.SUBSCRIPTION, price_full=7000),
        mk_group("GRP-0003", "БП БТ Первый год", "BRN-0002", archived=True),
        mk_group("GRP-0020", "ХГ Индивидуальные — Яковлева 🤸", "BRN-0001", GroupBillingMode.PER_VISIT, price_full=1800),
    ]
    branches = [mk_branch("BRN-0001", "Южная Битца"), mk_branch("BRN-0002", "Бутово Парк")]
    clients = [mk_client("CLT-0001", "Иванова Мария", phone="+79990001122")]
    tg = TeacherGroupRepoFake({"TCH-0001": ["GRP-0001", "GRP-0003"], "TCH-0002": ["GRP-0002"], "TCH-0003": ["GRP-0002"]})
    sg = StudentGroupRepoFake([
        StudentGroup("STU-0001", "GRP-0001"), StudentGroup("STU-0001", "GRP-0002", "2026-09"),
        StudentGroup("STU-0001", "GRP-9999"),
        StudentGroup("STU-0002", "GRP-0001"), StudentGroup("STU-0003", "GRP-0002", "", "2026-09"),
    ])
    repos = dict(
        student_repo=StudentRepoFake(students), teacher_repo=ByIdRepo([T1, T2, T3], "teacher_id"),
        group_repo=ByIdRepo(groups, "group_id"), branch_repo=ByIdRepo(branches, "branch_id"),
        client_repo=ClientRepoFake(clients), teacher_group_repo=tg, student_group_repo=sg,
    )
    repos["visibility"] = TeacherVisibilityService(repos["student_repo"], tg, sg)
    return repos


def _student_service(w):
    return StudentService(w["student_repo"], w["teacher_repo"], w["group_repo"], w["branch_repo"],
                          w["student_group_repo"], w["client_repo"], w["visibility"])


# ─── Карточки ─────────────────────────────────────────────────────────────────

def test_admin_student_card(monkeypatch):
    from bot.handlers.admin.students._base import _render_student_card
    install_fake_show_card(monkeypatch, "bot.handlers.admin.students._base")
    w = _world()
    cb = FakeCallbackQuery("student_card:STU-0001")
    run(_render_student_card(cb, "STU-0001", "students:list", _student_service(w)))
    assert_golden("admin_student_card_full", screen_dump(*cb.message.last))
    cb = FakeCallbackQuery("student_card:STU-0003")
    run(_render_student_card(cb, "STU-0003", "spage:1", _student_service(w)))
    assert_golden("admin_student_card_bare", screen_dump(*cb.message.last))
    cb = FakeCallbackQuery("student_card:STU-0404")
    run(_render_student_card(cb, "STU-0404", "students:list", _student_service(w)))
    assert cb.alerts == [("Ученик не найден", True)] and not cb.message.screens


def test_teacher_student_and_pair_cards(monkeypatch):
    from bot.handlers.teacher.partners.cards import _render_student_card
    install_fake_show_card(monkeypatch, "bot.handlers.teacher.partners.cards")
    w = _world()
    user = mk_user(7, teacher_id="TCH-0001")
    cb = FakeCallbackQuery("t_student_card:STU-0001", user_id=7)
    run(_render_student_card(cb, "STU-0001", user, w["student_repo"], w["visibility"]))
    assert_golden("teacher_student_card", screen_dump(*cb.message.last))
    cb = FakeCallbackQuery("t_pair_card:STU-0001", user_id=7)
    run(_render_student_card(cb, "STU-0001", user, w["student_repo"], w["visibility"], back_to_pairs=True))
    assert_golden("teacher_pair_card", screen_dump(*cb.message.last))
    # ученик не в группах педагога
    cb = FakeCallbackQuery("t_student_card:STU-0003", user_id=7)
    run(_render_student_card(cb, "STU-0003", user, w["student_repo"], w["visibility"]))
    assert cb.alerts == [("Ученик не в вашей группе", True)]


def test_group_cards_admin_and_teacher(monkeypatch):
    from bot.handlers.admin.branches._base import _render_group_card
    from bot.handlers.teacher.my_groups._base import _render_t_group_card
    install_fake_show_card(monkeypatch, "bot.handlers.admin.branches._base", "bot.handlers.teacher.my_groups._base")
    w = _world()
    for gid, name in (("GRP-0001", "per_visit"), ("GRP-0002", "subscription"), ("GRP-0003", "archived")):
        cb = FakeCallbackQuery(f"group_card:{gid}")
        run(_render_group_card(cb.message, gid, w["group_repo"], w["branch_repo"], w["teacher_repo"],
                               w["student_repo"], w["teacher_group_repo"], w["student_group_repo"]))
        assert_golden(f"admin_group_card_{name}", screen_dump(*cb.message.last))
    cb = FakeCallbackQuery("t_group:GRP-0001")
    run(_render_t_group_card(cb.message, "GRP-0001", w["group_repo"], w["branch_repo"], w["student_repo"], w["student_group_repo"]))
    assert_golden("teacher_group_card", screen_dump(*cb.message.last))


def test_admin_teacher_card(monkeypatch):
    from bot.handlers.admin.teachers.groups import _render_teacher_card
    install_fake_show_card(monkeypatch, "bot.handlers.admin.teachers.groups")
    w = _world()
    cb = FakeCallbackQuery("teacher_card:TCH-0001")
    run(_render_teacher_card(cb, "TCH-0001", w["teacher_repo"], w["teacher_group_repo"], w["group_repo"], w["branch_repo"]))
    assert_golden("admin_teacher_card", screen_dump(*cb.message.last))


# ─── Занятие ──────────────────────────────────────────────────────────────────

def _lesson_world():
    lessons = [
        mk_lesson("LES-000001", T1, "2026-09-05", 60, students=[("STU-0001", "Иванов Иван"), ("STU-0002", "Петрова Анна")]),
        mk_lesson("LES-000002", T1, "2026-08-20", 60, LessonType.GROUP, group_id="GRP-0001",
                  attendees="STU-0001:60:850,STU-0002:35:600,STU-0404:60:0"),
        mk_lesson("LES-000003", T2, "2026-09-06", 60, LessonType.GROUP, group_id="GRP-0020", attendees="STU-0001:60:1800"),
    ]
    return lessons


def test_lesson_detail_screens(monkeypatch):
    from bot.handlers.teacher.my_lessons.detail import cb_lesson_detail
    install_fake_show_card(monkeypatch, "bot.handlers.teacher.my_lessons.detail")
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    w = _world()
    lesson_repo = LessonRepoFake(_lesson_world())
    subs = SubmissionRepoFake([mk_submission("TCH-0001", "2026-08")])
    teacher = mk_user(7, teacher_id="TCH-0001")
    admin = mk_user(1, is_admin=True)

    cb = FakeCallbackQuery("lesson_detail:LES-000001", user_id=7)
    run(cb_lesson_detail(cb, teacher, lesson_repo, subs, w["group_repo"], w["student_repo"],
                         FakeState({"lm_mode": "view", "lm_filter_tag": "m-2026-09"})))
    assert_golden("lesson_detail_pair_teacher", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("lesson_detail:LES-000002", user_id=1)
    run(cb_lesson_detail(cb, admin, lesson_repo, subs, w["group_repo"], w["student_repo"], FakeState()))
    assert_golden("lesson_detail_group_locked_admin", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("lesson_detail:LES-000002", user_id=7)
    run(cb_lesson_detail(cb, teacher, lesson_repo, subs, w["group_repo"], w["student_repo"], FakeState({"lm_mode": "delete"})))
    assert_golden("lesson_detail_group_locked_teacher", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("lesson_detail:LES-000003", user_id=1)
    run(cb_lesson_detail(cb, admin, lesson_repo, subs, w["group_repo"], w["student_repo"],
                         FakeState({"t_stu_les_back": "t_stu_les_m:STU-0001:2026-09"})))
    assert_golden("lesson_detail_revenue_share", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("lesson_detail:LES-000003", user_id=7)   # чужое занятие для педагога
    run(cb_lesson_detail(cb, teacher, lesson_repo, subs, w["group_repo"], w["student_repo"], FakeState()))
    assert cb.alerts == [("Занятие не найдено", True)]


# ─── Занятия родителя (деньги считаются в хендлере) ───────────────────────────

def _parent_world():
    w = _world()
    lessons = [
        mk_lesson("LES-000001", T1, "2026-09-02", 45, students=[("STU-0001", "Иванов Иван")]),                      # 2000 ✅
        mk_lesson("LES-000002", T1, "2026-09-09", 60, students=[("STU-0001", "Иванов Иван"), ("STU-0002", "Петрова Анна")]),  # 1334 ⬜
        mk_lesson("LES-000003", T3, "2026-09-05", 90, LessonType.GROUP, group_id="GRP-0002", attendees="STU-0001:90:800"),
        mk_lesson("LES-000004", T3, "2026-09-07", 90, LessonType.GROUP, group_id="GRP-0002", attendees=None),         # абонемент — скрыт
        mk_lesson("LES-000005", T2, "2026-09-08", 45, students=[("STU-0001", "Иванов Иван")]),                      # прямая оплата
        mk_lesson("LES-000006", T1, "2026-09-10", 60, LessonType.GROUP, group_id="GRP-0020", attendees="STU-0001:60:1800"),
        mk_lesson("LES-000007", T1, "2026-09-02", 45, students=[("STU-0002", "Петрова Анна")]),                     # другой ребёнок
    ]
    payments = [mk_payment("PAY-1", "STU-0001", "2026-09", "TCH-0001", 2000, PaymentStatus.PAID, teacher_name="Река Станислав")]
    return w, PaymentService(PaymentRepoFake(payments), LessonRepoFake(lessons), w["teacher_repo"])


def test_parent_lessons_month_and_day(monkeypatch):
    from bot.handlers.client.my_lessons import _show_lessons
    monkeypatch.setattr(settings, "direct_pay_teacher_ids", "TCH-0002")
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    w, svc = _parent_world()

    cb = FakeCallbackQuery("cl_month:STU-0001:2026-09", user_id=100)
    run(_show_lessons(cb, w["student_repo"], svc, "2026-09", "STU-0001"))
    assert_golden("parent_lessons_month", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("cl_month_t:STU-0001:2026-09:TCH-0001", user_id=100)
    run(_show_lessons(cb, w["student_repo"], svc, "2026-09", "STU-0001", "TCH-0001"))
    assert_golden("parent_lessons_month_teacher_filter", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("cl_date:all:2026-09-02", user_id=100)
    run(_show_lessons(cb, w["student_repo"], svc, "2026-09-02", "all"))
    assert_golden("parent_lessons_day_all_children", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("cl_date:STU-0001:2026-09-03", user_id=100)
    run(_show_lessons(cb, w["student_repo"], svc, "2026-09-03", "STU-0001"))
    assert_golden("parent_lessons_day_empty", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("cl_month:STU-0001:2026-09", user_id=999)
    run(_show_lessons(cb, w["student_repo"], svc, "2026-09", "STU-0001"))
    assert cb.alerts == [("Нет доступа", True)]


# ─── Должники ─────────────────────────────────────────────────────────────────

class _DebtService:
    def __init__(self, debts):
        self.debts, self.calls = debts, []

    async def compute_debt_map(self, since_period=None):
        self.calls.append(since_period)
        return self.debts


def test_debtors_screen(monkeypatch):
    from bot.handlers.admin.debtors import _render_debtors
    monkeypatch.setattr("bot.handlers.admin.debtors._current_period", lambda: "2026-09")
    monkeypatch.setattr(settings, "debtors_since_period", "2026-07")
    w = _world()
    svc = _DebtService({
        "STU-0002": {"2026-08": 1300, "2026-09": 800},
        "STU-0003": {"2026-07": 7000, "2026-08": 7000},
        "STU-0001": {"2026-09": 2667},
        "STU-0404": {"2026-08": 500},
    })
    cb = FakeCallbackQuery("admin:debtors")
    run(_render_debtors(cb, 0, svc, w["student_repo"]))
    assert svc.calls == ["2026-07"]
    assert_golden("debtors_page", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("admin:debtors")
    run(_render_debtors(cb, 0, _DebtService({}), w["student_repo"]))
    assert_golden("debtors_empty", screen_dump(*cb.message.last))


# ─── История оплат ────────────────────────────────────────────────────────────

def _history_rows():
    return [
        mk_payment("PAY-1", "STU-0001", "2026-09", "TCH-0001", 2000, PaymentStatus.PAID, "Река Станислав",
                   paid_at="2026-09-03 18:20:00", method="yookassa_sbp", confirmed_by=0, comment="ЮКасса"),
        mk_payment("PAY-2", "STU-0001", "2026-09", "TCH-0001", 1334, teacher_name="Река Станислав"),
        mk_payment("PAY-3", "STU-0001", "2026-08", "SUB:GRP-0002", 7000, PaymentStatus.PAID, "Абонемент",
                   paid_at="2026-08-05 10:00:00", method="receipt_bank", confirmed_by=1, comment="чек"),
        mk_payment("PAY-4", "STU-0001", "2026-08", "TCH-0001", 500, PaymentStatus.PAID, "Река Станислав",
                   paid_at="2026-08-30 10:00:00", method="", confirmed_by=1, comment="переплата"),
        mk_payment("PAY-5", "STU-0001", "2026-07", "TCH-0001", 900, PaymentStatus.PAID, "Река Станислав",
                   paid_at="2026-07-30 10:00:00", method="", confirmed_by=0),
        mk_payment("PAY-6", "STU-0002", "2026-09", "TCH-0001", 700, teacher_name="Река Станислав"),
        mk_payment("PAY-7", "STU-0001", "2026-06", "TCH-0003", 0, teacher_name="Никишин Влад"),
    ]


def test_payment_history_screens():
    from bot.handlers.admin.payment_history import _render_periods, cb_payhist_group, cb_payhist_period
    w = _world()
    payment_repo = PaymentRepoFake(_history_rows())
    admin = mk_user(1, is_admin=True)

    cb = FakeCallbackQuery("payhist_g:GRP-0001")
    run(cb_payhist_group(cb, admin, w["group_repo"], w["student_repo"], w["student_group_repo"], payment_repo))
    assert_golden("payhist_group_students", screen_dump(*cb.message.last))

    cb = FakeCallbackQuery("payhist_stu:STU-0001")
    run(_render_periods(cb, mk_student("STU-0001", "Иванов Иван"), payment_repo))
    assert_golden("payhist_periods", screen_dump(*cb.message.last))

    for period in ("2026-09", "2026-08", "2026-07", "2026-06", "2026-05"):
        cb = FakeCallbackQuery(f"payhist_p:STU-0001:{period}")
        run(cb_payhist_period(cb, admin, w["student_repo"], payment_repo))
        assert_golden(f"payhist_period_{period}", screen_dump(*cb.message.last))


# ─── Расшифровка выплаты ──────────────────────────────────────────────────────

def test_payout_detail_lines():
    from bot.handlers.admin.payouts import _detail_lines
    w = _world()
    lessons = [
        mk_lesson("LES-1", T1, "2026-09-01", 60, LessonType.GROUP, group_id="GRP-0001", attendees="STU-0001:60:850"),
        mk_lesson("LES-2", T1, "2026-09-03", 60, LessonType.GROUP, group_id="GRP-0001", attendees="STU-0001:60:850"),
        mk_lesson("LES-3", T1, "2026-09-02", 45, LessonType.GROUP, group_id="GRP-0003"),
        mk_lesson("LES-4", T1, "2026-09-04", 60, LessonType.GROUP, group_id="GRP-4040"),
        mk_lesson("LES-5", T1, "2026-09-05", 45, students=[("STU-0001", "Иванов Иван")]),
        mk_lesson("LES-6", T1, "2026-09-06", 90, students=[("STU-0001", "Иванов Иван"), ("STU-0002", "Петрова Анна")]),
    ]
    lesson_repo = LessonRepoFake(lessons)
    lines = run(_detail_lines(T1, "2026-09", SalaryService(lesson_repo, None), lesson_repo, w["group_repo"], w["branch_repo"]))
    assert_golden("payout_detail", "\n".join(lines) + "\n")
    assert run(_detail_lines(T1, "2026-10", SalaryService(lesson_repo, None), lesson_repo, w["group_repo"], w["branch_repo"])) == [
        "Занятий нет.", "\n<b>Итого начислено: 0 ₽</b>",
    ]


# ─── Список учеников с поиском ────────────────────────────────────────────────

def test_student_search_paging():
    from bot.handlers.admin.students.listing import _filter_and_page
    students = sorted([mk_student(f"STU-{i:04d}", f"Ученик {i:02d}") for i in range(1, 24)]
                      + [mk_student("STU-0100", "Иванов Иван"), mk_student("STU-0101", "Иванова Ира")],
                      key=lambda s: s.name)
    pg = _filter_and_page(students, "ИВА", 0)
    assert ([s.student_id for s in pg.items], pg.total) == (["STU-0100", "STU-0101"], 2)
    pg = _filter_and_page(students, "", 1)
    assert ([s.name for s in pg.items], pg.total) == (["Ученик 19", "Ученик 20", "Ученик 21", "Ученик 22", "Ученик 23"], 25)
    pg = _filter_and_page(students, "zzz", 0)
    assert (pg.items, pg.total) == ([], 0)


# ─── «Нет доступа» — роли admin/teacher проверяют фильтры (tests/test_filters.py) ──

def test_access_denied_parent_handler():
    from bot.handlers.client.my_lessons import cb_client_lessons
    cb = FakeCallbackQuery("client:lessons", user_id=999)
    run(cb_client_lessons(cb, StudentRepoFake([mk_student(parent_tg_ids=[100])])))
    assert cb.alerts == [("Нет доступа", True)] and not cb.message.screens


def test_access_denied_athlete():
    from bot.handlers.athlete._base import athlete_of

    class _Diary:
        async def athlete_by_tg(self, tg_id):
            return None

    # Путь Message (фейк CallbackQuery не проходит isinstance-проверку хендлера — alert не снимаем)
    msg = FakeMessage(text="/start", user_id=999)
    assert run(athlete_of(msg, _Diary())) is None
    assert msg.screens == [("Кабинет спортсмена не привязан. Отправьте /start", None)]
