"""Клавиатуры кабинета спортсмена."""
from __future__ import annotations
from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.utils.dates import format_date_display


def athlete_welcome_text(student) -> str:
    return (
        f"🏃 <b>Кабинет спортсмена</b>\nСпортсмен: <b>{student.name}</b>\n\n"
        f"Выберите раздел:"
    )


def kb_athlete_menu(can_switch_parent: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Записать тренировку", callback_data="ath:log")],
        [InlineKeyboardButton(text="📓 Мои тренировки", callback_data="ath:entries")],
        [InlineKeyboardButton(text="📋 Мои задания", callback_data="ath:tasks")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="ath:stats")],
        [InlineKeyboardButton(text="🏆 Рейтинг", callback_data="ath:rating")],
    ]
    if can_switch_parent:
        rows.append([InlineKeyboardButton(text="👨‍👩‍👧 Кабинет родителя", callback_data="mode:client")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_menu_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Меню", callback_data="ath:menu")],
    ])


# ─── Запись тренировки ───────────────────────────────────────────────────────

def kb_log_date() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня", callback_data=f"athlog:date:{today.isoformat()}"),
            InlineKeyboardButton(text="Вчера", callback_data=f"athlog:date:{yesterday.isoformat()}"),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="athlog:date:manual")],
        [InlineKeyboardButton(text="« Отмена", callback_data="ath:menu")],
    ])


def kb_log_minutes() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m} мин", callback_data=f"athlog:min:{m}") for m in (30, 45, 60)],
        [InlineKeyboardButton(text=f"{m} мин", callback_data=f"athlog:min:{m}") for m in (90, 120, 180)],
        [InlineKeyboardButton(text="✍️ Другое число", callback_data="athlog:min:manual")],
        [
            InlineKeyboardButton(text="« Назад", callback_data="athlog:back:date"),
            InlineKeyboardButton(text="« Отмена", callback_data="ath:menu"),
        ],
    ])


def kb_log_topics(topics: list[str], selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(topics):
        mark = "✅" if i in selected else "⬜"
        row.append(InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"athlog:tp:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text=f"➡️ Дальше ({len(selected)})", callback_data="athlog:tp_done",
    )])
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data="athlog:back:min"),
        InlineKeyboardButton(text="« Отмена", callback_data="ath:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_log_tasks(tasks: list[tuple[str, str]], selected: set[str]) -> InlineKeyboardMarkup:
    """tasks: [(task_id, label)]."""
    rows = []
    for tid, label in tasks:
        mark = "✅" if tid in selected else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"athlog:tk:{tid}")])
    rows.append([InlineKeyboardButton(
        text=f"➡️ Дальше ({len(selected)})", callback_data="athlog:tk_done",
    )])
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data="athlog:back:tp"),
        InlineKeyboardButton(text="« Отмена", callback_data="ath:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_log_comment(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без комментария", callback_data="athlog:skip_comment")],
        [
            InlineKeyboardButton(text="« Назад", callback_data=back_cb),
            InlineKeyboardButton(text="« Отмена", callback_data="ath:menu"),
        ],
    ])


def kb_log_done() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Ещё тренировка", callback_data="ath:log")],
        [InlineKeyboardButton(text="🏆 Рейтинг", callback_data="ath:rating")],
        [InlineKeyboardButton(text="« Меню", callback_data="ath:menu")],
    ])


# ─── Мои тренировки ──────────────────────────────────────────────────────────

def kb_period_toggle(cb_prefix: str, period: str, this: str, prev: str) -> list[InlineKeyboardButton]:
    """Ряд «Этот месяц / Прошлый месяц»; активный помечен ●."""
    def _btn(label: str, p: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"● {label}" if p == period else label, callback_data=f"{cb_prefix}:{p}",
        )
    return [_btn("Этот месяц", this), _btn("Прошлый месяц", prev)]


def kb_entries(entries: list, period: str, this: str, prev: str) -> InlineKeyboardMarkup:
    rows = []
    for e in entries[:20]:
        grade = f"⭐{e.grade}" if e.grade else "🆕"
        topics = ", ".join(e.topics[:2]) + ("…" if len(e.topics) > 2 else "")
        rows.append([InlineKeyboardButton(
            text=f"{grade} {format_date_display(e.date)[:5]} · {e.minutes} мин · {topics}",
            callback_data=f"athent:view:{e.entry_id}",
        )])
    rows.append(kb_period_toggle("athent:list", period, this, prev))
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="ath:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_entry_detail(entry, can_delete: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_delete:
        rows.append([InlineKeyboardButton(text="🗑 Удалить запись", callback_data=f"athent:del:{entry.entry_id}")])
    rows.append([InlineKeyboardButton(text="« К списку", callback_data=f"athent:list:{entry.date[:7]}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="ath:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_entry_delete_confirm(entry) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"athent:del_ok:{entry.entry_id}"),
            InlineKeyboardButton(text="« Нет", callback_data=f"athent:view:{entry.entry_id}"),
        ],
    ])


# ─── Статистика и рейтинг ────────────────────────────────────────────────────

def kb_stats(period: str, this: str, prev: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        kb_period_toggle("ath:stats", period, this, prev),
        [InlineKeyboardButton(text="🏆 Рейтинг", callback_data=f"ath:rating:{period}:all")],
        [InlineKeyboardButton(text="« Меню", callback_data="ath:menu")],
    ])


def kb_rating(
    cb_prefix: str, period: str, this: str, prev: str,
    topics: list[tuple[int, str]], active: str, back_cb: str, back_label: str = "« Меню",
) -> InlineKeyboardMarkup:
    """topics: [(индекс в ALL_TOPICS, название)]; active: 'all' или индекс строкой."""
    rows = [kb_period_toggle(f"{cb_prefix}", period, this, prev)]
    # период в toggle идёт без темы → хендлер подставит 'all'
    mark = "● " if active == "all" else ""
    rows.append([InlineKeyboardButton(text=f"{mark}Все танцы", callback_data=f"{cb_prefix}:{period}:all")])
    row: list[InlineKeyboardButton] = []
    for idx, name in topics:
        mark = "● " if active == str(idx) else ""
        row.append(InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"{cb_prefix}:{period}:{idx}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=back_label, callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
