"""«🏫 Группа в саду» в боте MAX — экраны без транспорта (решение владельца 10.10.2026)."""
from __future__ import annotations

from .types import cb

HOME = "go:home"


def kgroup_pick_screen(students: list) -> tuple:
    rows = [[cb(s.name, f"client_kgroup:{s.student_id}")] for s in students]
    rows.append([cb("« Меню", HOME)])
    return "🏫 Для кого указать группу в детском саду?", rows


def kgroup_prompt_screen(student) -> tuple:
    now = student.kindergarten_group
    rows = ([[cb("🗑 Стереть", f"kgroup_clear:{student.student_id}")]] if now else []) + [[cb("« Меню", HOME)]]
    return (f"🏫 <b>Группа в детском саду — {student.name}</b>\n"
            f"Сейчас: {now or 'не указана'}\n\n"
            "Напишите номер или название группы в саду одним сообщением — по нему педагог забирает детей "
            "на занятие."), rows


def kgroup_saved_screen(student, value: str) -> tuple:
    text = (f"✅ Группа в саду для {student.name}: <b>{value}</b>" if value
            else f"Группа в саду для {student.name} стёрта.")
    return text, [[cb("« Меню", HOME)]]
