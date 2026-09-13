"""Golden-снимки клавиатур: пагинация списков и карточки (тексты и callback байт-в-байт)."""
from bot.handlers.admin.debtors import _kb_debtors
from bot.keyboards.admin import kb_back, kb_confirm, kb_student_card, kb_student_paged, kb_teacher_card
from bot.keyboards.client import kb_lessons_month_filter
from bot.keyboards.teacher import kb_lesson_detail, kb_lesson_list
from bot.models.enums import LessonType
from config.settings import settings
from tests.fakes import assert_golden, markup_dump, mk_lesson, mk_student, mk_teacher


def _lessons():
    t = mk_teacher("TCH-0001", "Река Станислав")
    return [
        mk_lesson("LES-000010", t, "2026-09-12", 45, students=[("STU-0001", "Иванов Иван")]),
        mk_lesson("LES-000011", t, "2026-09-12", 60, students=[("STU-0001", "Иванов Иван"), ("STU-0002", "Петрова Анна")]),
        mk_lesson("LES-000012", t, "2026-09-11", 90, LessonType.GROUP, group_id="GRP-0001", attendees="STU-0001:90:850"),
        mk_lesson("LES-000013", t, "2026-09-10", 60, LessonType.GROUP, group_id="GRP-0020",
                  attendees="STU-0001:60:1800,STU-0002:60:1800"),
        mk_lesson("LES-000014", t, "2026-08-30", 45,
                  students=[("STU-0003", "Сидоров Пётр"), ("STU-0004", "Кузнецова Мария"), ("STU-0005", "Орлов Олег")]),
        mk_lesson("LES-000015", t, "2026-08-29", 45),
    ]


def test_kb_lesson_list_teacher_first_page(monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    kb = kb_lesson_list(_lessons(), page=0, page_size=4, locked_ids={"LES-000014", "LES-000015"})
    assert_golden("kb_lesson_list_teacher_p0", markup_dump(kb))


def test_kb_lesson_list_teacher_last_page_with_filters(monkeypatch):
    monkeypatch.setattr(settings, "revenue_share_groups", "GRP-0020:50")
    kb = kb_lesson_list(_lessons(), page=1, page_size=4, locked_ids={"LES-000014", "LES-000015"},
                        filter_month="2026-09", filter_type="individual")
    assert_golden("kb_lesson_list_teacher_p1_month_individual", markup_dump(kb))


def test_kb_lesson_list_single_day_admin_variant():
    kb = kb_lesson_list(_lessons()[:2], page=0, page_size=10, filter_date="2026-09-12",
                        back_cb="aedl_dates:TCH-0001", type_cb_prefix="aedl_type:TCH-0001", show_type_filter=False)
    assert_golden("kb_lesson_list_admin_day", markup_dump(kb))


def test_kb_student_paged_navigation():
    students = [mk_student(f"STU-{i:04d}", f"Ученик {i:02d}") for i in range(1, 46)]
    assert_golden("kb_student_paged_p0", markup_dump(kb_student_paged(students[:20], 0, 45)))
    assert_golden("kb_student_paged_p1", markup_dump(kb_student_paged(students[20:40], 1, 45)))
    assert_golden("kb_student_paged_p2", markup_dump(kb_student_paged(students[40:], 2, 45)))


def test_kb_debtors_pages():
    assert_golden("kb_debtors_single_page", markup_dump(_kb_debtors(0, 1, can_remind=True)))
    assert_golden("kb_debtors_middle_page", markup_dump(_kb_debtors(1, 3, can_remind=False)))
    assert_golden("kb_debtors_last_page", markup_dump(_kb_debtors(2, 3, can_remind=True)))


def test_kb_student_card_variants():
    full = kb_student_card(
        "STU-0001", has_partner=True, back_cb="students:list",
        tier_toggle=("short", "🕐 Переключить на полный (60 мин / 850₽)"), has_groups=True,
        client_rows=[[("Отвязать клиента", "student_client_unbind:STU-0001")],
                     [("🔗 Ссылка для спортсмена", "athreg:link:STU-0001")]],
    )
    assert_golden("kb_student_card_full", markup_dump(full))
    bare = kb_student_card("STU-0002", has_partner=False)
    assert_golden("kb_student_card_bare", markup_dump(bare))


def test_kb_lesson_detail_variants():
    lesson = _lessons()[2]
    assert_golden("kb_lesson_detail_teacher_locked", markup_dump(kb_lesson_detail(lesson, locked=True)))
    assert_golden("kb_lesson_detail_admin_locked_guest",
                  markup_dump(kb_lesson_detail(lesson, locked=True, back_cb="admin:edit_lesson", can_add_guest=True, is_admin=True)))
    assert_golden("kb_lesson_detail_open_guest",
                  markup_dump(kb_lesson_detail(lesson, back_cb="lessons_page:0:m-2026-09", can_add_guest=True)))


def test_kb_teacher_card_back_confirm():
    assert_golden("kb_teacher_card", markup_dump(kb_teacher_card("TCH-0001")))
    assert_golden("kb_back", markup_dump(kb_back("admin:menu")))
    assert_golden("kb_confirm", markup_dump(kb_confirm("del:ok", "del:no", "🗑 Удалить")))


def test_kb_lessons_month_filter():
    teachers = [("TCH-0001", "Река С."), ("TCH-0002", "Клецова А.")]
    assert_golden("kb_lessons_month_filter_all",
                  markup_dump(kb_lessons_month_filter("STU-0001", "2026-09", teachers, active="all", pay_amount=2667)))
    assert_golden("kb_lessons_month_filter_teacher",
                  markup_dump(kb_lessons_month_filter("STU-0001", "2026-09", teachers, active="TCH-0002", pay_amount=800)))
    assert_golden("kb_lessons_month_filter_all_children",
                  markup_dump(kb_lessons_month_filter("all", "2026-09", teachers[:1], pay_amount=500)))
