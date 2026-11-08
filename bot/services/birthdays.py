"""Дата рождения ученика (решение владельца 08.11.2026): хранится в `students.birth_date` (кол. 19, `YYYY-MM-DD`,
`scripts/setup_birth_date.py`), нужна, чтобы знать возраст — «в каком возрасте танцует» (возрастная группа ФТСАРР
у бальников) — и дни рождения. Правят администратор и педагог своих групп (`PUT /students/{sid}/birth`), спортсмен
и родитель видят в дневнике."""
from __future__ import annotations

import re
from datetime import date

# Возрастные группы ФТСАРР — по возрасту, которого спортсмен достигает в календарном году (не по дню рождения)
AGE_GROUPS = [(6, "Дети 0"), (9, "Дети 1"), (11, "Дети 2"), (13, "Юниоры 1"), (15, "Юниоры 2"), (18, "Молодёжь"), (999, "Взрослые")]
MIN_YEAR = 1950


def parse_birth_date(raw) -> str | None:
    """«04.03.2014», «4.3.2014», «2014-03-04» → «2014-03-04»; пусто → «»; не дата или дата в будущем → None."""
    value = "".join(str(raw or "").split())
    if not value:
        return ""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
    if m:
        d, mo, y = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        dt = date(y, mo, d)
    except ValueError:
        return None
    if dt > date.today() or y < MIN_YEAR:
        return None
    return dt.isoformat()


def display(birth: str) -> str:
    """«2014-03-04» → «04.03.2014»."""
    try:
        return date.fromisoformat(birth).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def age_on(birth: str, on: date) -> int | None:
    """Полных лет на дату `on`."""
    try:
        b = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def age_label(age: int) -> str:
    """«1 год», «3 года», «12 лет»."""
    n = age % 100
    if 11 <= n <= 19:
        word = "лет"
    else:
        n %= 10
        word = "год" if n == 1 else "года" if 2 <= n <= 4 else "лет"
    return f"{age} {word}"


def age_group(birth: str, year: int) -> str:
    """Возрастная группа ФТСАРР в календарном году `year` — по году рождения."""
    try:
        reached = year - int(birth[:4])
    except (TypeError, ValueError):
        return ""
    return next(label for limit, label in AGE_GROUPS if reached <= limit)


def view(student, ballroom: bool = False, today: date | None = None) -> dict:
    """Блок карточки: дата, возраст словами и (у бальников) возрастная группа ФТСАРР; без даты — всё пусто."""
    today = today or date.today()
    birth = parse_birth_date(getattr(student, "birth_date", "")) or ""   # в листе могли набрать «04.03.2014» руками
    age = age_on(birth, today) if birth else None
    return {"birthDate": birth, "display": display(birth) if birth else "", "age": age,
            "ageLabel": age_label(age) if age is not None else "",
            "ageGroup": age_group(birth, today.year) if birth and ballroom else ""}
