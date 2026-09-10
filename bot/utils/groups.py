"""Утилиты по группам."""
from __future__ import annotations

from config.settings import settings


def hide_service_groups(group_ids) -> set:
    """Убирает технические revenue-share группы (тарифы индивидуальных,
    напр. «ХГ Индивидуальные — Яковлева») из списков, видимых педагогу.
    Занятия в них создаются только через флоу «Индивидуальное»."""
    return set(group_ids) - set(settings.revenue_share_group_map)
