"""Утилиты по группам."""
from __future__ import annotations

from config.settings import settings


def hide_service_groups(group_ids) -> set:
    """Убирает технические revenue-share группы (тарифы индивидуальных,
    напр. «ХГ Индивидуальные — Яковлева») из списков, видимых педагогу.
    Занятия в них создаются только через флоу «Индивидуальное»."""
    return set(group_ids) - set(settings.revenue_share_group_map)


def merged_title(names: list[str]) -> str:
    """Название объединённого занятия: общие слова в начале и конце один раз —
    «ЮБ сад ХГ 16:00–17:00 🤸» + «ЮБ сад ХГ 17:00–18:00 🤸» → «ЮБ сад ХГ 16:00–17:00 + 17:00–18:00 🤸»."""
    names = [n for n in names if n]
    if len(names) < 2:
        return names[0] if names else ""
    words = [n.split() for n in names]
    head = 0
    while all(len(w) > head + 1 for w in words) and len({w[head] for w in words}) == 1:
        head += 1
    tail = 0
    while all(len(w) > head + tail + 1 for w in words) and len({w[-1 - tail] for w in words}) == 1:
        tail += 1
    middles = [" ".join(w[head:len(w) - tail]) for w in words]
    return " ".join([*words[0][:head], " + ".join(middles), *(words[0][len(words[0]) - tail:] if tail else [])])


def lesson_group_title(lesson, names: dict, default: str = "") -> str:
    """Название группы занятия; у объединённого — обе группы (`names`: group_id → название или Group)."""
    titles = [getattr(names[g], "name", names[g]) for g in lesson.group_ids if g in names]
    return merged_title(titles) if titles else default
