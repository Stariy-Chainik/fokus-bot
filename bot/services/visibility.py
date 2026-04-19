from __future__ import annotations

from bot.models import Student
from bot.repositories import StudentRepository, TeacherGroupRepository


class TeacherVisibilityService:
    """Видимость ученика педагогу вычисляется через group_id.

    Педагог видит ученика ⇔ student.group_id ∈ teacher_groups[teacher_id].
    Источник истины — таблицы students и teacher_groups; отдельной связки
    teacher↔student в системе нет.
    """

    def __init__(
        self,
        student_repo: StudentRepository,
        teacher_group_repo: TeacherGroupRepository,
    ) -> None:
        self._student_repo = student_repo
        self._teacher_group_repo = teacher_group_repo

    async def visible_group_ids(self, teacher_id: str) -> set[str]:
        return set(await self._teacher_group_repo.get_groups_for_teacher(teacher_id))

    async def students_for_teacher(self, teacher_id: str) -> list[Student]:
        group_ids = await self.visible_group_ids(teacher_id)
        students = [
            s for s in await self._student_repo.get_all()
            if s.group_id and s.group_id in group_ids
        ]
        students.sort(key=lambda s: s.name)
        return students

    async def students_in_group_for_teacher(
        self, teacher_id: str, group_id: str,
    ) -> list[Student]:
        group_ids = await self.visible_group_ids(teacher_id)
        if group_id not in group_ids:
            return []
        students = [
            s for s in await self._student_repo.get_all()
            if s.group_id == group_id
        ]
        students.sort(key=lambda s: s.name)
        return students

    async def teachers_for_student(self, student_id: str) -> list[str]:
        student = await self._student_repo.get_by_id(student_id)
        if not student or not student.group_id:
            return []
        return await self._teacher_group_repo.get_teachers_for_group(student.group_id)

    async def is_visible(self, teacher_id: str, student_id: str) -> bool:
        student = await self._student_repo.get_by_id(student_id)
        if not student or not student.group_id:
            return False
        group_ids = await self.visible_group_ids(teacher_id)
        return student.group_id in group_ids
