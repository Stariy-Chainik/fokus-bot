"""LessonService: create() с датой/замком, delete() с замком, preview_period()."""
from datetime import date

import pytest

from bot.models.enums import GroupBillingMode, LessonType
from bot.services import LessonService
from tests.fakes import ByIdRepo, LessonRepoFake, SubmissionRepoFake, mk_group, mk_lesson, mk_submission, mk_teacher, run


def _svc(lessons=(), subs=(), salary_service=None):
    t = mk_teacher()
    return LessonService(LessonRepoFake(lessons), SubmissionRepoFake(subs), ByIdRepo([t], "teacher_id"),
                         salary_service=salary_service), t


def test_create_rejects_future_date():
    svc, t = _svc()
    with pytest.raises(ValueError, match="в будущем"):
        run(svc.create(t, LessonType.INDIVIDUAL, "2999-01-01", 45, student_1_id="STU-1", student_1_name="A"))


def test_create_respects_period_lock_unless_bypassed():
    svc, t = _svc(subs=[mk_submission("TCH-0001", "2026-08")])
    with pytest.raises(PermissionError, match="уже сдан"):
        run(svc.create(t, LessonType.INDIVIDUAL, "2026-08-10", 45, student_1_id="STU-1", student_1_name="A"))
    lesson = run(svc.create(t, LessonType.INDIVIDUAL, "2026-08-10", 45, student_1_id="STU-1", student_1_name="A",
                            bypass_period_lock=True))
    assert lesson.lesson_id == "LES-000001" and lesson.earned == 0 and lesson.date == "2026-08-10"
    assert svc._lesson_repo.items == [lesson]


def test_create_group_lesson_not_deduplicated_and_ids_sequential():
    svc, t = _svc()
    first = run(svc.create(t, LessonType.GROUP, "2026-09-01", 60, attendees="STU-1:60:850", group_id="GRP-1"))
    second = run(svc.create(t, LessonType.GROUP, "2026-09-01", 60, attendees="STU-1:60:850", group_id="GRP-1"))
    assert (first.lesson_id, second.lesson_id) == ("LES-000001", "LES-000002")


def test_delete_paths():
    t = mk_teacher()
    lessons = [mk_lesson("LES-1", t, "2026-08-10", students=[("STU-1", "A")]),
               mk_lesson("LES-2", t, "2026-09-10", students=[("STU-1", "A")])]
    svc, _ = _svc(lessons, subs=[mk_submission("TCH-0001", "2026-08")])
    assert run(svc.delete("LES-404")) is False
    with pytest.raises(PermissionError):
        run(svc.delete("LES-1"))
    assert run(svc.delete("LES-2")) is True
    assert run(svc.delete("LES-1", bypass_period_lock=True)) is True
    assert svc._lesson_repo.deleted == ["LES-2", "LES-1"]


def test_preview_period_without_salary_service_uses_pure_formula():
    t = mk_teacher(rate_group=1000, rate_for_teacher=1500)
    lessons = [mk_lesson("LES-1", t, "2026-09-01", 45, students=[("STU-1", "A")]),
               mk_lesson("LES-2", t, "2026-09-02", 60, LessonType.GROUP, group_id="GRP-1", attendees=None),
               mk_lesson("LES-3", t, "2026-10-02", 60, students=[("STU-1", "A")])]  # другой месяц
    svc, _ = _svc(lessons)
    got_lessons, teacher, total = run(svc.preview_period("TCH-0001", "2026-09"))
    assert [ls.lesson_id for ls in got_lessons] == ["LES-1", "LES-2"]
    assert teacher.teacher_id == "TCH-0001"
    assert total == 1500 + round(1000 * 60 / 45)  # 1500 + 1333


def test_preview_period_with_salary_service_and_unknown_teacher():
    class _Salary:
        async def total_for(self, teacher, period):
            return 42

    svc, _ = _svc([], salary_service=_Salary())
    assert run(svc.preview_period("TCH-0001", "2026-09"))[2] == 42
    with pytest.raises(ValueError, match="не найден"):
        run(svc.preview_period("TCH-0404", "2026-09"))


def test_add_guest_prices_and_duplicates():
    """Гость: price_full для PER_VISIT (тариф SHORT не учитывается — B7), 0 иначе, дубль → None."""
    t = mk_teacher()
    lesson = mk_lesson("LES-1", t, "2026-09-01", 60, LessonType.GROUP, group_id="GRP-1", attendees="STU-1:60:850")
    svc, _ = _svc([lesson])
    per_visit = mk_group("GRP-1", billing_mode=GroupBillingMode.PER_VISIT, price_full=850, price_short=600)
    assert run(svc.add_guest(lesson, "STU-2", per_visit)) == "STU-1:60:850,STU-2:60:850"
    assert svc._lesson_repo.attendees_updates == [("LES-1", "STU-1:60:850,STU-2:60:850")]
    lesson.attendees = "STU-1:60:850,STU-2:60:850"
    assert run(svc.add_guest(lesson, "STU-2", per_visit)) is None                       # уже отмечен
    assert run(svc.add_guest(lesson, "STU-3", mk_group("GRP-2"))) == "STU-1:60:850,STU-2:60:850,STU-3:60:0"
    assert run(svc.add_guest(mk_lesson("LES-2", t, "2026-09-02", 45, LessonType.GROUP, group_id="GRP-9"), "STU-1", None)) == "STU-1:45:0"


def test_can_submit_period_from_25th():
    assert LessonService.can_submit_period(date(2026, 9, 24), "2026-09") is False
    assert LessonService.can_submit_period(date(2026, 9, 25), "2026-09") is True
    assert LessonService.can_submit_period(date(2026, 9, 1), "2026-08") is True    # прошлый месяц — всегда
    assert LessonService.can_submit_period(date(2026, 9, 30), "2026-10") is False
