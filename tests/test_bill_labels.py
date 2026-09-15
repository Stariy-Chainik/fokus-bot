"""Подпись строки счёта: групповые занятия — названием группы, индивидуальные — педагогом."""
from bot.handlers.admin.bills.helpers import _bill_detail_lines
from bot.models.enums import LessonType
from bot.screens.parent_bills import render_bill_detail
from bot.services import PaymentService
from bot.services.payment_ledger import BillAggregate, TeacherLedger
from bot.utils.bill_format import build_bill_text
from tests.fakes import ByIdRepo, LessonRepoFake, PaymentRepoFake, mk_group, mk_lesson, mk_student, mk_teacher, run

STU = mk_student("STU-0007", "Татаркина София")


def _service(lessons):
    teacher = mk_teacher("TCH-0002", "Клецова Ангелина", rate_group=1000, rate_for_teacher=1500, rate_for_student=1800)
    groups = [mk_group("GRP-0019", "БП Джаз"), mk_group("GRP-0015", "БП ОФП")]
    return PaymentService(
        PaymentRepoFake([]), LessonRepoFake(lessons), ByIdRepo([teacher], "teacher_id"),
        group_repo=ByIdRepo(groups, "group_id"),
    ), teacher


def _group_lesson(lesson_id, teacher, date, group_id):
    return mk_lesson(lesson_id, teacher, date, duration=60, lesson_type=LessonType.GROUP,
                     attendees="STU-0007:60:800,STU-0001:60:800", group_id=group_id)


def test_group_only_bill_is_labelled_with_group_name():
    teacher = mk_teacher("TCH-0002", "Клецова Ангелина")
    svc, _ = _service([_group_lesson("LES-1", teacher, "2026-09-12", "GRP-0019")])
    bills = run(svc.compute_bills_for_student_period("STU-0007", "2026-09"))
    agg = bills["TCH-0002"]                       # ключ — по-прежнему педагог (учёт оплат)
    assert (agg.name, agg.group, agg.total) == ("БП Джаз", True, 800)

    ledgers = run(svc.ledger_for(STU, "2026-09"))
    assert ledgers["TCH-0002"].group is True and ledgers["TCH-0002"].name == "БП Джаз"


def test_two_groups_of_one_teacher_are_listed():
    teacher = mk_teacher("TCH-0002", "Клецова Ангелина")
    svc, _ = _service([_group_lesson("LES-1", teacher, "2026-09-12", "GRP-0019"),
                       _group_lesson("LES-2", teacher, "2026-09-14", "GRP-0015")])
    agg = run(svc.compute_bills_for_student_period("STU-0007", "2026-09"))["TCH-0002"]
    assert agg.name == "БП Джаз, БП ОФП" and agg.group is True


def test_mixed_individual_and_group_keeps_teacher_name():
    teacher = mk_teacher("TCH-0002", "Клецова Ангелина")
    svc, _ = _service([_group_lesson("LES-1", teacher, "2026-09-12", "GRP-0019"),
                       mk_lesson("LES-2", teacher, "2026-09-13", students=[("STU-0007", "Татаркина София")])])
    agg = run(svc.compute_bills_for_student_period("STU-0007", "2026-09"))["TCH-0002"]
    assert agg.name == "Клецова Ангелина" and agg.group is False and agg.total == 800 + 1800


def test_screens_say_group_instead_of_teacher():
    item = type("B", (), {"date": "2026-09-12", "lesson_id": "LES-1", "duration_min": 60, "amount": 800, "lesson_type": "group"})()
    agg = BillAggregate("БП Джаз", 800, items=[item], group=True)
    text, total = build_bill_text("Татаркина София", ["БП Джаз"], "2026-09", {"TCH-0002": agg})
    assert "👥 <b>БП Джаз</b>" in text and total == 800

    lines = _bill_detail_lines("Татаркина София", "2026-09", {"TCH-0002": agg}, [])
    assert "👥 <b>БП Джаз</b> — 800 руб. — ⏳ Счёт не создан — 800 руб." in lines

    ledger = TeacherLedger(teacher_id="TCH-0002", name="БП Джаз", accrued=800, paid=0, items=[item], group=True)
    detail = render_bill_detail("2026-09", [(STU, {"TCH-0002": ledger})])
    assert "<b>Группа: БП Джаз</b>" in detail.lines
    assert not any("Педагог:" in line for line in detail.lines)
