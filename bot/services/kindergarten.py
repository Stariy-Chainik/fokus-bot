"""Группа ребёнка в детском саду (решение владельца 10.10.2026).

Садовые группы школы («БП Сад», «ЮБ сад БТ», «ЮБ сад ХГ …») забирают детей из групп детского сада, поэтому
у ребёнка такой группы есть поле «группа в саду» — номер или название, текстом (`students.kindergarten_group`).
Заполняет родитель (кабинет Telegram, бот MAX) или педагог (кабинет), видит и правит администратор.
Садовая группа школы — по слову «сад» в названии.
"""
from __future__ import annotations

import re

_KG = re.compile(r"\bсад\b", re.IGNORECASE)


def is_kindergarten_group(group) -> bool:
    return group is not None and bool(_KG.search(group.name or ""))


async def kindergarten_group_names(student_id: str, student_group_repo, groups: dict) -> list[str]:
    """Садовые группы школы, где ребёнок сейчас занимается (groups — {group_id: Group})."""
    gids = await student_group_repo.get_groups_for_student(student_id)
    return [groups[g].name for g in gids if g in groups and is_kindergarten_group(groups[g])]


def kindergarten_group_ids(groups: dict) -> set[str]:
    return {gid for gid, g in groups.items() if is_kindergarten_group(g)}


def clean_value(raw) -> str | None:
    """Номер или название группы из формы: пробелы схлопнуты; None — длиннее 40 символов."""
    from bot.repositories.student_repo import KINDERGARTEN_GROUP_MAX
    value = " ".join(str(raw or "").split())
    return value if len(value) <= KINDERGARTEN_GROUP_MAX else None
