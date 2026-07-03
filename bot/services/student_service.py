"""Сервис сборки данных по ученику для админских экранов.

Хендлер остаётся тонким: сервис читает репозитории и возвращает DTO,
хендлер только рендерит текст и клавиатуру.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from bot.models import Student, Group, Client
from bot.repositories import (
    StudentRepository, TeacherRepository, GroupRepository,
    BranchRepository, StudentGroupRepository, ClientRepository,
)
from .visibility import TeacherVisibilityService


@dataclass
class StudentCardGroup:
    """Строка блока «Группы»: group=None — битая ссылка из student_groups."""
    group_id: str
    group: Group | None = None
    branch_name: str = ""  # имя филиала, либо branch_id, если филиал не найден


@dataclass
class StudentCard:
    """Данные карточки ученика — всё для отрисовки без обращений к репозиториям."""
    student: Student                  # group_ids уже проставлены
    teacher_names: list[str] = field(default_factory=list)  # пусто — «не привязан»
    partner: Student | None = None    # None — партнёра нет или битая ссылка
    groups: list[StudentCardGroup] = field(default_factory=list)
    primary_group: Group | None = None  # первая найденная группа (для блока тарифа)
    client: Client | None = None      # None — клиент не задан или битая ссылка


class StudentService:
    def __init__(
        self,
        student_repo: StudentRepository,
        teacher_repo: TeacherRepository,
        group_repo: GroupRepository,
        branch_repo: BranchRepository,
        student_group_repo: StudentGroupRepository,
        client_repo: ClientRepository,
        visibility: TeacherVisibilityService,
    ) -> None:
        self._student_repo = student_repo
        self._teacher_repo = teacher_repo
        self._group_repo = group_repo
        self._branch_repo = branch_repo
        self._student_group_repo = student_group_repo
        self._client_repo = client_repo
        self._visibility = visibility

    async def get_student_card(self, student_id: str) -> StudentCard | None:
        """Собрать данные карточки ученика; None — ученик не найден."""
        student = await self._student_repo.get_by_id(student_id)
        if not student:
            return None
        student.group_ids = await self._student_group_repo.get_groups_for_student(student_id)

        teacher_ids = await self._visibility.teachers_for_student(student_id)
        if teacher_ids:
            teachers_map = {t.teacher_id: t.name for t in await self._teacher_repo.get_all()}
            # Если педагог не найден — показываем сам id (как и раньше).
            teacher_names = [teachers_map.get(tid, tid) for tid in teacher_ids]
        else:
            teacher_names = []

        partner = None
        if student.partner_id:
            partner = await self._student_repo.get_by_id(student.partner_id)

        groups: list[StudentCardGroup] = []
        primary_group: Group | None = None
        for gid in student.group_ids:
            g = await self._group_repo.get_by_id(gid)
            if g:
                if primary_group is None:
                    primary_group = g
                branch = await self._branch_repo.get_by_id(g.branch_id)
                groups.append(StudentCardGroup(
                    group_id=gid, group=g,
                    branch_name=branch.name if branch else g.branch_id,
                ))
            else:
                groups.append(StudentCardGroup(group_id=gid))

        client = None
        if student.client_id:
            client = await self._client_repo.get_by_id(student.client_id)

        return StudentCard(
            student=student,
            teacher_names=teacher_names,
            partner=partner,
            groups=groups,
            primary_group=primary_group,
            client=client,
        )
