"""Главное меню родителя — общие ряды кнопок для Telegram и MAX."""
from __future__ import annotations

from datetime import date
from html import escape

from bot.utils.dates import current_period, month_name_ru
from .parent_lessons import _hours, _lessons_word
from .types import cb, maxapp, webapp


def welcome_text(students: list, summary: list | None = None) -> str:
    """Приветствие меню; summary (`parent_views.menu_summary`) — сводка над кнопками (бот MAX)."""
    names = [escape(s.name) for s in students]
    who = ("Ребёнок: <b>" + names[0] + "</b>") if len(names) == 1 \
        else "Дети: <b>" + ", ".join(names) + "</b>"
    body = summary_text(summary, several=len(students) > 1) if summary else ""
    return f"👨‍👩‍👧 <b>Личный кабинет родителя</b>\n{who}\n\n" + (f"{body}\n\n" if body else "") + "Выберите раздел:"


def _month(ym: str) -> str:
    return month_name_ru(int(ym[5:7])).lower()


def summary_text(summary: list, several: bool = False, today: str = "") -> str:
    """Сводка как в кабинете: «Оплатить за …» (текущий месяц) / «Долг за …» (закрытые), заявки, которые ждут
    школу, «Всё оплачено» и занятия месяца. Ребёнок без строк пропускается."""
    current, today = current_period(), today or date.today().isoformat()
    blocks = []
    for c in summary:
        lines = []
        for ym, rest in c.due:
            lines.append(f"💳 Оплатить за {_month(ym)}: <b>{rest} руб.</b>" if ym >= current
                         else f"⚠️ Долг за {_month(ym)}: <b>{rest} руб.</b>")
        for kind, amount, ym in c.pending:
            what = "наличные" if kind == "cash" else "чек"
            lines.append(f"⏳ Ждёт подтверждения школы: {what}" + (f" {amount} руб." if amount else "")
                         + f" за {_month(ym)}")
        if not c.due and c.accrued:
            lines.append("✅ Всё оплачено")
        if c.lessons:
            last = "сегодня" if c.last == today else f"{c.last[8:10]}.{c.last[5:7]}"
            lines.append(f"📅 {month_name_ru(int(current[5:7]))}: {_lessons_word(c.lessons)} · {_hours(c.minutes)}, "
                         f"последнее {last}")
        if lines:
            blocks.append("\n".join(([f"<b>{escape(c.name)}</b>"] if several else []) + lines))
    return "\n\n".join(blocks)


def menu_rows(can_switch_athlete: bool = False, platform: str = "tg",
              receipt_email: bool = True, cabinet_url: str = "", kindergarten: bool = False,
              max_app: bool = False, max_cabinet_only: bool = False) -> list:
    if platform == "max" and max_cabinet_only:
        # MAX — только кабинет (MAX_PARENT_CABINET_ONLY): счета, оплата, занятия, «кто привязан» внутри него
        return [[maxapp("📱 Открыть кабинет", "parent")], [cb("➕ Добавить ребёнка", "client:add_child")]]
    if platform == "max":
        # MAX: занятия (расписание без денег), счета и оплата; дневник/email — позже.
        # max_app — кабинет родителя мини-приложением MAX (MAX_MINIAPP_ENABLED, решение владельца 14.10.2026)
        return [
            *([[maxapp("📱 Открыть кабинет", "parent")]] if max_app else []),
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
