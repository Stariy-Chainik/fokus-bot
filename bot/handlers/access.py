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
from config.settings import settings


def is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def can_teacher_bill(user: User | None) -> bool:
    """Педагог из BILLING_TEACHER_IDS: выставление счетов ученикам своих групп."""
    return is_teacher(user) and user.teacher_id in settings.billing_teacher_id_set


def is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


def is_teacher_or_admin(user: User | None) -> bool:
    return user is not None and (user.teacher_id is not None or user.is_admin)
