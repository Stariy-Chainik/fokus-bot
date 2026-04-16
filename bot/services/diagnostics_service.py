import logging
from dataclasses import dataclass

from bot.repositories import LessonRepository, TeacherRepository, StudentRepository

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticsReport:
    lessons_with_missing_teacher: list[str]
    lessons_with_missing_student: list[str]
    errors: list[str]


class DiagnosticsService:
    def __init__(
        self,
        lesson_repo: LessonRepository,
        teacher_repo: TeacherRepository,
        student_repo: StudentRepository,
    ) -> None:
        self._lesson_repo = lesson_repo
        self._teacher_repo = teacher_repo
        self._student_repo = student_repo

    async def run_consistency_check(self) -> DiagnosticsReport:
        lessons = await self._lesson_repo.get_all()
        teacher_ids = {t.teacher_id for t in await self._teacher_repo.get_all()}
        student_ids = {s.student_id for s in await self._student_repo.get_all()}

        missing_teacher: list[str] = []
        missing_student: list[str] = []
        for ls in lessons:
            if ls.teacher_id not in teacher_ids:
                missing_teacher.append(ls.lesson_id)
            for sid in (ls.student_1_id, ls.student_2_id):
                if sid and sid not in student_ids:
                    missing_student.append(ls.lesson_id)
                    break

        logger.info(
            "Диагностика: lessons без teacher=%d, lessons без student=%d",
            len(missing_teacher), len(missing_student),
        )
        return DiagnosticsReport(
            lessons_with_missing_teacher=missing_teacher,
            lessons_with_missing_student=missing_student,
            errors=[],
        )
