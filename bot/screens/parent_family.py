"""«Кто привязан к ребёнку» в боте MAX — экраны без транспорта (данные — parent_unlink.family_view).

Тот же смысл, что экран `p.family` кабинета Telegram: кто видит ребёнка, «Отвязать» лишнего —
заявка администратору с необязательной причиной (решение владельца 06.10.2026).
"""
from __future__ import annotations

from .types import cb

HOME = "go:home"


def who(p: dict) -> str:
    if p.get("name"):
        return p["name"]
    return "Родитель в " + ("MAX" if p.get("platform") == "max" else "Telegram")


def _since(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) >= 10 else ""


def family_pick_screen(students: list) -> tuple:
    rows = [[cb(s.name, f"client_family:{s.student_id}")] for s in students]
    rows.append([cb("« Меню", HOME)])
    return "👥 Чьи привязки показать?", rows


def family_screen(child: dict, many: bool) -> tuple:
    lines = [f"👥 <b>Кто привязан к ученику {child['name']}</b>", "",
             "Эти люди видят счета и занятия ребёнка и получают уведомления школы.", ""]
    rows = []
    for p in child["parents"]:
        bits = [f"@{p['username']}" if p.get("username") else "",
                "MAX" if p["platform"] == "max" else "Telegram",
                f"с {_since(p['since'])}" if p.get("since") else ""]
        tail = " · ".join(b for b in bits if b)
        if p["me"]:
            lines.append(f"• <b>Вы</b>{' — ' + p['name'] if p.get('name') else ''} · {tail}")
            continue
        lines.append(f"• {who(p)} · {tail}" + (" — ⏳ заявка на отвязку у администратора" if p["pending"] else ""))
        if not p["pending"]:
            rows.append([cb(f"🚫 Отвязать: {who(p)[:40]}", f"famunl:{child['id']}:{p['key']}")])
    lines += ["", "Лишнего отвязывает администратор школы: нажмите «Отвязать», заявка уйдёт ему на проверку. "
                  "Если вы сами привязались по ошибке — «↩️ Это не мой ребёнок» в меню."]
    if many:
        rows.append([cb("« Дети", "client:family")])
    rows.append([cb("« Меню", HOME)])
    return "\n".join(lines), rows


def unlink_other_confirm_screen(child: dict, p: dict) -> tuple:
    sid, key = child["id"], p["key"]
    return (f"Отвязать <b>{who(p)}</b> от ученика <b>{child['name']}</b>?\n\n"
            "Заявка уйдёт администратору школы. После его подтверждения этот человек перестанет видеть счета "
            "и занятия ребёнка и получать уведомления."), [
        [cb("📨 Отправить заявку", f"famunl_do:{sid}:{key}")],
        [cb("✍️ Указать причину", f"famunl_why:{sid}:{key}")],
        [cb("« Назад", f"client_family:{sid}")],
    ]


def unlink_reason_prompt(student_id: str) -> tuple:
    return ("✍️ Напишите причину одним сообщением — она уйдёт администратору вместе с заявкой.",
            [[cb("« Отмена", f"client_family:{student_id}")]])


def unlink_sent_screen(student_id: str, already: bool = False) -> tuple:
    text = ("⏳ Заявка на отвязку этого родителя уже у администратора. Когда он решит, вам придёт сообщение."
            if already else "📨 Заявка отправлена администратору. Когда он решит, вам придёт сообщение.")
    return text, [[cb("« Кто привязан", f"client_family:{student_id}")], [cb("« Меню", HOME)]]
