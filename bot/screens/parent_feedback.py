"""«💬 Написать руководителю» в боте MAX — экраны без транспорта (решение владельца 11.11.2026)."""
from __future__ import annotations

from bot.services.parent_feedback import KIND_ICONS, KINDS, MAX_LEN

from .types import cb

HOME = "go:home"


def feedback_pick_screen(students: list) -> tuple:
    rows = [[cb(s.name, f"client_fb:{s.student_id}")] for s in students]
    rows.append([cb("« Меню", HOME)])
    return "💬 О ком хотите написать руководителю?", rows


def feedback_kind_screen(student) -> tuple:
    rows = [[cb(f"{KIND_ICONS[k]} {label}", f"client_fbk:{student.student_id}:{k}")] for k, label in KINDS.items()]
    rows.append([cb("« Меню", HOME)])
    return (f"💬 <b>Написать руководителю студии</b>\nРебёнок: {student.name}\n\n"
            "Сообщение уйдёт лично руководителю, педагоги его не увидят. Выберите, о чём напишете:"), rows


def feedback_prompt_screen(student, kind: str) -> tuple:
    return (f"{KIND_ICONS[kind]} <b>{KINDS[kind]}</b> · {student.name}\n\n"
            f"Напишите одним сообщением, что хотите передать руководителю (до {MAX_LEN} знаков)."), [[cb("« Меню", HOME)]]


def feedback_sent_screen() -> tuple:
    return ("✅ Отправлено руководителю студии. Ответ придёт сюда же, в этот чат.", [[cb("« Меню", HOME)]])


def feedback_rate_screen() -> tuple:
    return ("Вы писали руководителю меньше десяти минут назад — подождите немного, прежде чем писать снова.",
            [[cb("« Меню", HOME)]])
