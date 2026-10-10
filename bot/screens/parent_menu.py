"""Главное меню родителя — общие ряды кнопок для Telegram и MAX."""
from __future__ import annotations

from .types import cb, webapp


def welcome_text(students: list) -> str:
    names = [s.name for s in students]
    who = ("Ребёнок: <b>" + names[0] + "</b>") if len(names) == 1 \
        else "Дети: <b>" + ", ".join(names) + "</b>"
    return f"👨‍👩‍👧 <b>Личный кабинет родителя</b>\n{who}\n\nВыберите раздел:"


def menu_rows(can_switch_athlete: bool = False, platform: str = "tg",
              receipt_email: bool = True, cabinet_url: str = "", kindergarten: bool = False) -> list:
    if platform == "max":
        # MAX: занятия (расписание без денег), счета и оплата; дневник/email — позже
        return [
            [cb("📅 Занятия", "client:lessons")],
            [cb("💳 Оплата занятий", "client:my_bills")],
            *([[cb("🏫 Группа в саду", "client:kgroup")]] if kindergarten else []),   # дети садовых групп
            [cb("➕ Добавить ребёнка", "client:add_child")],
            [cb("👥 Кто привязан", "client:family")],
            [cb("↩️ Это не мой ребёнок", "client:unlink")],
        ]
    if cabinet_url:      # родитель в Telegram — только кабинет: счета, оплата, занятия, дневник внутри него
        rows = [[webapp("📱 Открыть кабинет", cabinet_url)], [cb("➕ Добавить ребёнка", "client:add_child")]]
    else:
        rows = [
            [cb("📅 Занятия", "client:lessons")],
            [cb("💳 Оплата занятий", "client:my_bills")],
            [cb("📓 Дневник тренировок", "client:diary")],
            [cb("➕ Добавить ребёнка", "client:add_child")],
        ]
    if receipt_email:
        rows.append([cb("✉️ Email для чеков", "client:email")])
    if can_switch_athlete and platform == "tg":
        rows.append([cb("🏃 Кабинет спортсмена", "mode:athlete")])
    return rows
