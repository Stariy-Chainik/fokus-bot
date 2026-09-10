"""Темы тренировок для дневника спортсмена.

Бальники выбирают танцы, гимнастки (группы с «ХГ» в названии) — предметы.
Список фиксирован в коде: темы попадают в статистику и рейтинг по танцам,
поэтому свободный ввод не используется («Другое» + комментарий).
"""
from __future__ import annotations

BALLROOM: list[str] = [
    "Медленный вальс", "Танго", "Венский вальс", "Фокстрот", "Квикстеп",
    "Самба", "Ча-ча-ча", "Румба", "Пасодобль", "Джайв",
    "ОФП/растяжка", "Другое",
]

RHYTHMIC: list[str] = [
    "Скакалка", "Обруч", "Мяч", "Булавы", "Лента", "Без предмета",
    "Хореография", "ОФП/растяжка", "Другое",
]

# Единый список для фильтра рейтинга (индексы в callback): бальные + предметы ХГ.
ALL_TOPICS: list[str] = BALLROOM + [t for t in RHYTHMIC if t not in BALLROOM]

_RG_MARK = "ХГ"


def is_rhythmic(group_names: list[str]) -> bool:
    return any(_RG_MARK in (name or "") for name in group_names)


def topics_for_student(group_names: list[str]) -> list[str]:
    """Список тем по группам ученика: есть группа «…ХГ…» — гимнастика, иначе бальные."""
    return list(RHYTHMIC if is_rhythmic(group_names) else BALLROOM)
