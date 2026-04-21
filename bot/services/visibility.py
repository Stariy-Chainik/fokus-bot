from __future__ import annotations

from bot.models import Student
from bot.repositories import (
    StudentRepository, TeacherGroupRepository, StudentGroupRepository,
)


class TeacherVisibilityService:
    """Видимость ученика педагогу вычисляется через множественные группы.

    Педагог видит ученика ⇔ пересечение `teacher_groups[teacher_id]`
    и `student_groups[student_id]` непусто. Источник истины — таблицы
    `teacher_groups` и `student_groups`.
    """

    def __init__(
        self,
        student_repo: StudentRepository,
        teacher_group_repo: TeacherGroupRepository,
        student_group_repo: StudentGroupRepository,
    ) -> None:
        self._student_repo = student_repo
        self._teacher_group_repo = teacher_group_repo
        self._student_group_repo = student_group_repo

    async def visible_group_ids(self, teacher_id: str) -> set[str]:
        return set(await self._teacher_group_repo.get_groups_for_teacher(teacher_id))

    async def _student_to_groups_map(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for sg in await self._student_group_repo.get_all():
            mapping.setdefault(sg.student_id, []).append(sg.group_id)
        return mapping

    async def students_for_teacher(self, teacher_id: str) -> list[Student]:
        my_groups = await self.visible_group_ids(teacher_id)
        if not my_groups:
            return []
        sg_map = await self._student_to_groups_map()
        students = []
        for s in await self._student_repo.get_all():
            gids = sg_map.get(s.student_id, [])
            if set(gids) & my_groups:
                s.group_ids = gids
                students.append(s)
        students.sort(key=lambda s: s.name)
        return students

    async def students_in_group_for_teacher(
        self, teacher_id: str, group_id: str,
    ) -> list[Student]:
        my_groups = await self.visible_group_ids(teacher_id)
        if group_id not in my_groups:
            return []
        member_ids = set(await self._student_group_repo.get_students_for_group(group_id))
        if not member_ids:
            return []
        sg_map = await self._student_to_groups_map()
        students = []
        for s in await self._student_repo.get_all():
            if s.student_id in member_ids:
                s.group_ids = sg_map.get(s.student_id, [])
                students.append(s)
        students.sort(key=lambda s: s.name)
        return students

    async def teachers_for_student(self, student_id: str) -> list[str]:
        gids = await self._student_group_repo.get_groups_for_student(student_id)
        if not gids:
            return []
        teacher_ids: set[str] = set()
        for gid in gids:
            for tid in await self._teacher_group_repo.get_teachers_for_group(gid):
                teacher_ids.add(tid)
        return sorted(teacher_ids)

    async def is_visible(self, teacher_id: str, student_id: str) -> bool:
        my_groups = await self.visible_group_ids(teacher_id)
        if not my_groups:
            return False
        student_groups = set(await self._student_group_repo.get_groups_for_student(student_id))
        return bool(my_groups & student_groups)

    async def groups_of_student(self, student_id: str) -> list[str]:
        """Публичный доступ к списку групп ученика — для хендлеров, которым
        нужно заполнить `student.group_ids` или принять решение о видимости."""
        return await self._student_group_repo.get_groups_for_student(student_id)
