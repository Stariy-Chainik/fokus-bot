"""Сервис сборки данных по ученику для админских экранов.

Хендлер остаётся тонким: сервис читает репозитории и возвращает DTO,
хендлер только рендерит текст и клавиатуру.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from bot.models import Student, Group, Client
from bot.models.enums import GroupBillingMode, StudentGroupTier
from bot.repositories import (
    StudentRepository, TeacherRepository, GroupRepository,
    BranchRepository, StudentGroupRepository, ClientRepository,
)
from .visibility import TeacherVisibilityService


class TierToggleError(str, Enum):
    """Причина отказа переключения тарифа (тексты алертов — в хендлере)."""
    STUDENT_NOT_FOUND = "student_not_found"
    NO_GROUPS = "no_groups"
    NO_PER_VISIT_GROUP = "no_per_visit_group"


@dataclass
class StudentCardGroup:
    """Строка блока «Группы»: group=None — битая ссылка из student_groups."""
    group_id: str
    group: Group | None = None
    branch_name: str = ""  # имя филиала, либо branch_id, если филиал не найден


@dataclass
class CreatedStudent:
    """Результат создания ученика с (опциональной) группой."""
    student: Student
    group: Group | None = None   # None — группа не задана или не найдена
    branch_name: str = ""        # имя филиала, либо «—», если филиал не найден


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

    async def create_with_group(self, name: str, group_id: str) -> CreatedStudent:
        """Создать ученика и, если group_id непуст, добавить в группу."""
        student = await self._student_repo.add(name)
        group = None
        branch_name = ""
        if group_id:
            await self._student_group_repo.add(student.student_id, group_id)
            group = await self._group_repo.get_by_id(group_id)
            if group:
                branch = await self._branch_repo.get_by_id(group.branch_id)
                branch_name = branch.name if branch else "—"
        return CreatedStudent(student=student, group=group, branch_name=branch_name)

    async def delete_student(self, student_id: str) -> bool:
        """Удалить ученика: сначала членства в группах, затем строку ученика.

        Пара рвётся внутри student_repo.delete(). False — ученик не найден.
        """
        await self._student_group_repo.remove_all_for_student(student_id)
        return await self._student_repo.delete(student_id)

    async def toggle_tier(self, student_id: str) -> TierToggleError | None:
        """Переключить тариф SHORT↔FULL; None — успех, иначе причина отказа.

        Тариф переключается относительно первой PER_VISIT группы ученика.
        """
        student = await self._student_repo.get_by_id(student_id)
        if not student:
            return TierToggleError.STUDENT_NOT_FOUND
        gids = await self._student_group_repo.get_groups_for_student(student_id)
        if not gids:
            return TierToggleError.NO_GROUPS
        per_visit_group = None
        for gid in gids:
            g = await self._group_repo.get_by_id(gid)
            if g and g.billing_mode == GroupBillingMode.PER_VISIT:
                per_visit_group = g
                break
        if per_visit_group is None:
            return TierToggleError.NO_PER_VISIT_GROUP
        new_tier = (
            StudentGroupTier.FULL if student.group_tier == StudentGroupTier.SHORT
            else StudentGroupTier.SHORT
        )
        await self._student_repo.update_tier(student_id, new_tier)
        return None

    async def pairs_in_group(self, group_id: str) -> list[tuple[Student, Student]]:
        """Пары группы: (a, b) упорядочены по имени, симметричные связи дедуплицированы.

        Достаточно членства одного из двух — партнёр может быть вне группы.
        Сортировка пар по имени первого ученика.
        """
        all_students = await self._student_repo.get_all()
        member_ids = set(await self._student_group_repo.get_students_for_group(group_id))
        grp_students = [s for s in all_students if s.student_id in member_ids]
        by_id = {s.student_id: s for s in all_students}
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[Student, Student]] = []
        for s in grp_students:
            if not s.partner_id:
                continue
            partner = by_id.get(s.partner_id)
            if not partner:
                continue
            key = tuple(sorted([s.student_id, partner.student_id]))
            if key in seen:
                continue
            seen.add(key)
            a, b = (s, partner) if s.name <= partner.name else (partner, s)
            pairs.append((a, b))
        pairs.sort(key=lambda p: p[0].name)
        return pairs

    async def soloists_in_group(self, group_id: str) -> list[Student]:
        """Солисты группы (члены без партнёра), по имени."""
        member_ids = set(await self._student_group_repo.get_students_for_group(group_id))
        return sorted(
            [s for s in await self._student_repo.get_all()
             if s.student_id in member_ids and not s.partner_id],
            key=lambda s: s.name,
        )

    async def partner_candidates(self, student: Student) -> list[tuple[Student, bool]] | None:
        """Кандидаты в партнёры: ученики хотя бы с одной общей группой.

        None — у ученика вообще нет групп (отдельный экран в UI).
        Кандидат — (ученик, «у него уже есть партнёр»), сортировка по имени;
        сам ученик и его текущий партнёр исключены.
        """
        student_gids = set(
            await self._student_group_repo.get_groups_for_student(student.student_id)
        )
        if not student_gids:
            return None
        sg_map = await self._student_group_repo.get_map_by_student()
        all_students = sorted(await self._student_repo.get_all(), key=lambda s: s.name)
        candidates: list[tuple[Student, bool]] = []
        for other in all_students:
            if other.student_id == student.student_id:
                continue
            other_gids = set(sg_map.get(other.student_id, []))
            if not (other_gids & student_gids):
                continue
            if other.student_id == student.partner_id:
                continue
            candidates.append((other, bool(other.partner_id)))
        return candidates

    async def partner_candidates_in_group(
        self, student: Student, group_id: str,
    ) -> list[tuple[Student, bool]]:
        """Кандидаты в партнёры среди членов одной группы (поток «Создать пару»)."""
        member_ids = set(await self._student_group_repo.get_students_for_group(group_id))
        all_students = sorted(await self._student_repo.get_all(), key=lambda s: s.name)
        candidates: list[tuple[Student, bool]] = []
        for other in all_students:
            if other.student_id == student.student_id:
                continue
            if other.student_id not in member_ids:
                continue
            if other.student_id == student.partner_id:
                continue
            candidates.append((other, bool(other.partner_id)))
        return candidates

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
