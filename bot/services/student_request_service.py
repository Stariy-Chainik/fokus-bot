"""Сервис обработки заявок педагогов на новых учеников.

Транзакционная часть (mark_resolved → создать/привязать ученика → группа)
живёт здесь; уведомления и тексты экранов остаются в хендлерах.
"""
from __future__ import annotations
from enum import Enum

from bot.models import Student, StudentRequest
from bot.models.enums import RequestStatus
from bot.repositories import (
    StudentRepository, StudentGroupRepository, StudentRequestRepository,
)


class LinkExistingOutcome(str, Enum):
    """Итог привязки существующего ученика к заявке (тексты — в хендлере)."""
    ALREADY_IN_GROUP = "already_in_group"
    ADDED_TO_GROUP = "added_to_group"
    NO_GROUP = "no_group"


class StudentRequestService:
    def __init__(
        self,
        student_request_repo: StudentRequestRepository,
        student_repo: StudentRepository,
        student_group_repo: StudentGroupRepository,
    ) -> None:
        self._student_request_repo = student_request_repo
        self._student_repo = student_repo
        self._student_group_repo = student_group_repo

    async def approve_create(
        self, req: StudentRequest, resolved_by_tg_id: int,
    ) -> Student | None:
        """APPROVED + создать ученика + добавить в группу заявки.

        None — заявку уже обработали параллельно (mark_resolved вернул False);
        ученик в этом случае не создаётся.
        """
        if not await self._student_request_repo.mark_resolved(
            req.request_id, RequestStatus.APPROVED, resolved_by_tg_id,
        ):
            return None
        student = await self._student_repo.add(name=req.student_name)
        if req.group_id:
            await self._student_group_repo.add(student.student_id, req.group_id)
        return student

    async def approve_link_existing(
        self, req: StudentRequest, student_id: str, resolved_by_tg_id: int,
    ) -> LinkExistingOutcome | None:
        """APPROVED + при необходимости добавить существующего ученика в группу заявки.

        None — заявку уже обработали параллельно. Ученик может состоять в N
        группах — проверяем членство, а не «основную» группу.
        """
        if not await self._student_request_repo.mark_resolved(
            req.request_id, RequestStatus.APPROVED, resolved_by_tg_id,
        ):
            return None
        student_gids = set(
            await self._student_group_repo.get_groups_for_student(student_id)
        )
        already_in = bool(req.group_id) and req.group_id in student_gids
        if already_in:
            return LinkExistingOutcome.ALREADY_IN_GROUP
        if req.group_id:
            await self._student_group_repo.add(student_id, req.group_id)
            return LinkExistingOutcome.ADDED_TO_GROUP
        return LinkExistingOutcome.NO_GROUP
