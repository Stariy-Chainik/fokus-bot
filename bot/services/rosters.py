"""Состав группы как список Student — одно место вместо копий «member_ids ∩ get_all()».

Ключ сортировки — параметр: экраны исторически сортируют по `name` или по
`name.lower()`, а рассылки идут в порядке листа (`key=None`).
"""
from __future__ import annotations

from typing import Callable

from bot.models import Student


def BY_NAME(s: Student) -> str:  # noqa: N802 — используется как константа-ключ
    return s.name


def BY_NAME_CI(s: Student) -> str:  # noqa: N802
    return s.name.lower()


async def group_members(
    student_repo, student_group_repo, group_id: str, *,
    key: Callable[[Student], str] | None = BY_NAME, include_left: bool = False,
) -> list[Student]:
    """Ученики группы (объекты Student). include_left — вместе с ушедшими из абонементной группы."""
    if include_left:
        member_ids = set(await student_group_repo.get_students_for_group(group_id, include_left=True))
    else:
        member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = [s for s in await student_repo.get_all() if s.student_id in member_ids]
    if key is not None:
        members.sort(key=key)
    return members
