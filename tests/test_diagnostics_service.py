"""DiagnosticsService.run_consistency_check — сироты по педагогу и ученику."""
from bot.models.enums import LessonType
from bot.services import DiagnosticsService
from tests.fakes import ByIdRepo, LessonRepoFake, StudentRepoFake, mk_lesson, mk_student, mk_teacher, run


def test_consistency_check_reports_orphans():
    t = mk_teacher("TCH-0001")
    ghost = mk_teacher("TCH-0099", "Уволен")
    lessons = [
        mk_lesson("LES-1", t, "2026-09-01", students=[("STU-0001", "Иванов")]),
        mk_lesson("LES-2", ghost, "2026-09-02", students=[("STU-0001", "Иванов")]),           # нет педагога
        mk_lesson("LES-3", t, "2026-09-03", students=[("STU-0001", "Иванов"), ("STU-0404", "Нет")]),  # нет ученика
        mk_lesson("LES-4", ghost, "2026-09-04", students=[("STU-0404", "Нет")]),               # и то и другое
        mk_lesson("LES-5", t, "2026-09-05", lesson_type=LessonType.GROUP, attendees="STU-0404:60:850", group_id="GRP-1"),
    ]
    svc = DiagnosticsService(LessonRepoFake(lessons), ByIdRepo([t], "teacher_id"), StudentRepoFake([mk_student("STU-0001")]))
    report = run(svc.run_consistency_check())
    assert report.lessons_with_missing_teacher == ["LES-2", "LES-4"]
    # занятие с двумя битыми учениками учитывается один раз; attendees группового не проверяются
    assert report.lessons_with_missing_student == ["LES-3", "LES-4"]
    assert report.errors == []


def test_consistency_check_clean():
    t = mk_teacher()
    svc = DiagnosticsService(LessonRepoFake([mk_lesson("LES-1", t, "2026-09-01", students=[("STU-0001", "Иванов")])]),
                             ByIdRepo([t], "teacher_id"), StudentRepoFake([mk_student()]))
    report = run(svc.run_consistency_check())
    assert (report.lessons_with_missing_teacher, report.lessons_with_missing_student) == ([], [])
