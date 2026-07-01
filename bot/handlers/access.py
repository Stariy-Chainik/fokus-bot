"""Единые предикаты доступа для хендлеров.

Раньше эти проверки копировались в каждом файле-хендлере (`_is_admin` — в 9,
`_is_teacher` — в 6). Здесь один источник истины.

Важно: `is_teacher` — строго педагог (не админ), а `is_teacher_or_admin`
дополнительно пропускает администратора. В record_lesson.py локальный
`_is_teacher` исторически имел admin-inclusive-семантику — он замаплен на
`is_teacher_or_admin`, чтобы поведение не изменилось.
"""
from __future__ import annotations

from bot.models import User


def is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


def is_teacher_or_admin(user: User | None) -> bool:
    return user is not None and (user.teacher_id is not None or user.is_admin)
