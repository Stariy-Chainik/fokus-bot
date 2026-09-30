"""Занятия ребёнка за месяц для мессенджеров (MAX): расписание без денег.

Как экран «Занятия» в кабинете родителя (решение владельца 24.09.2026): кто вёл, группа,
длительность; ни сумм, ни статусов оплаты — деньги только в счетах.
"""
from __future__ import annotations

from bot.utils.dates import format_date_short_with_wd, period_label

from .parent_bills import HOME
from .types import cb

LESSONS = "mxl"          # mxl:{student_id}:{YYYY-MM}


def lessons_cb(student_id: str, period: str) -> str:
    return f"{LESSONS}:{student_id}:{period}"


def child_select_screen(students: list, period: str) -> tuple:
    rows = [[cb(s.name, lessons_cb(s.student_id, period))] for s in students]
    rows.append([cb("« Меню", HOME)])
    return "📅 <b>Занятия</b>\nВыберите ребёнка:", rows


def _lessons_word(n: int) -> str:
    m10, m100 = n % 10, n % 100
    word = "занятие" if m10 == 1 and m100 != 11 else "занятия" if 2 <= m10 <= 4 and not 12 <= m100 <= 14 else "занятий"
    return f"{n} {word}"


def _hours(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} ч {m} мин" if h and m else f"{h} ч" if h else f"{m} мин"


def lessons_month_screen(name: str, student_id: str, period: str, items: list,
                         prev_ym: str | None, next_ym: str | None, several: bool) -> tuple:
    """items — строки `student_lessons.month_lessons` (date, type, group, durationMin, teacher)."""
    lines = [f"📅 <b>Занятия · {period_label(period)}</b>", f"Ребёнок: <b>{name}</b>"]
    if items:
        lines.append(f"Всего: {_lessons_word(len(items))} · {_hours(sum(x['durationMin'] for x in items))}")
        day = ""
        for x in items:
            if x["date"] != day:
                day = x["date"]
                lines.append(f"\n<b>{format_date_short_with_wd(day)}</b>")
            what = x["group"] if x["type"] == "group" and x["group"] else "индивидуальное"
            icon = "👥" if x["type"] == "group" else "👤"
            lines.append(f"{icon} {what} · {x['durationMin']} мин · {x['teacher']}")
    else:
        lines.append("\nЗанятий в этом месяце нет.")
    nav = []
    if prev_ym:
        nav.append(cb(f"◀ {period_label(prev_ym)}", lessons_cb(student_id, prev_ym)))
    if next_ym:
        nav.append(cb(f"{period_label(next_ym)} ▶", lessons_cb(student_id, next_ym)))
    rows = [nav] if nav else []
    if several:
        rows.append([cb("👨‍👩‍👧 Другой ребёнок", f"client:lessons:{period}")])
    rows.append([cb("« Меню", HOME)])
    return "\n".join(lines), rows
