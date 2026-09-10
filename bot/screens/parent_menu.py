"""Главное меню родителя — общие ряды кнопок для Telegram и MAX."""
from __future__ import annotations

from .types import cb


def welcome_text(students: list) -> str:
    names = [s.name for s in students]
    who = ("Ребёнок: <b>" + names[0] + "</b>") if len(names) == 1 \
        else "Дети: <b>" + ", ".join(names) + "</b>"
    return f"👨‍👩‍👧 <b>Личный кабинет родителя</b>\n{who}\n\nВыберите раздел:"


def menu_rows(can_switch_athlete: bool = False, platform: str = "tg") -> list:
    if platform == "max":
        # Релиз 1 в MAX: счета и оплата; занятия/дневник/email появятся позже
        return [
            [cb("💳 Оплата занятий", "client:my_bills")],
            [cb("➕ Добавить ребёнка", "client:add_child")],
        ]
    rows = [
        [cb("📅 Занятия", "client:lessons")],
        [cb("💳 Оплата занятий", "client:my_bills")],
        [cb("📓 Дневник тренировок", "client:diary")],
        [cb("➕ Добавить ребёнка", "client:add_child")],
        [cb("✉️ Email для чеков", "client:email")],
    ]
    if can_switch_athlete and platform == "tg":
        rows.append([cb("🏃 Кабинет спортсмена", "mode:athlete")])
    return rows
