"""Текстовые представления дневника спортсмена — общие для спортсмена, педагога и родителя."""
from __future__ import annotations
from typing import Optional

from bot.utils.dates import format_date_display, display_period


def minutes_human(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


def stars(grade: Optional[int]) -> str:
    return "★" * grade + "☆" * (5 - grade) if grade else "без оценки"


def entry_short(e) -> str:
    """Одна строка списка: 10.09 · 60 мин · Самба, Румба · ★★★★☆"""
    topics = ", ".join(e.topics) if e.topics else "—"
    grade = f" · ⭐{e.grade}" if e.grade else " · 🆕"
    return f"{format_date_display(e.date)[:5]} · {e.minutes} мин · {topics}{grade}"


def entry_full(e, tasks_by_id: dict | None = None, grader_name: str = "") -> str:
    lines = [
        f"📅 <b>{format_date_display(e.date)}</b> · {minutes_human(e.minutes)}",
        f"🎵 {', '.join(e.topics) if e.topics else '—'}",
    ]
    if e.task_ids:
        names = []
        for tid in e.task_ids:
            t = (tasks_by_id or {}).get(tid)
            names.append(f"{t.exercise} ({t.minutes} мин)" if t else tid)
        lines.append("📋 Задания: " + "; ".join(names))
    if e.comment:
        lines.append(f"💬 {e.comment}")
    if e.grade:
        who = f" · {grader_name}" if grader_name else ""
        lines.append(f"\n⭐ Оценка: <b>{e.grade}/5</b> {stars(e.grade)}{who}")
        if e.grade_comment:
            lines.append(f"📝 {e.grade_comment}")
    else:
        lines.append("\n⏳ Оценки педагога пока нет")
    return "\n".join(lines)


def stats_text(st, period: str, place: Optional[int] = None, total: int = 0) -> str:
    lines = [f"📊 <b>{display_period(period)}</b>"]
    if st.sessions == 0:
        lines.append("Тренировок пока нет.")
        return "\n".join(lines)
    lines.append(f"Тренировок: <b>{st.sessions}</b> · в зале: <b>{minutes_human(st.total_minutes)}</b>")
    if st.avg_grade is not None:
        lines.append(f"Средняя оценка: <b>{st.avg_grade}</b> (оценено {st.graded} из {st.sessions})")
    else:
        lines.append("Оценок пока нет")
    lines.append(f"Очки рейтинга: <b>{st.points}</b>")
    if place:
        lines.append(f"Место в рейтинге: <b>{place}</b> из {total}")
    if st.by_topic:
        lines.append("\nПо танцам:")
        for t, m in st.by_topic.items():
            lines.append(f"  • {t} — {minutes_human(m)}")
    return "\n".join(lines)


def leaderboard_text(
    rows: list, period: str, topic: Optional[str], highlight: Optional[str] = None, limit: int = 0,
) -> str:
    from bot.services.diary_service import place_icon
    title = f"🏆 <b>Рейтинг · {display_period(period)}</b>" + (f" · {topic}" if topic else "")
    lines = [title, "<i>очки = минуты × оценка педагога</i>\n"]
    shown = rows[:limit] if limit else rows
    for r in shown:
        me = " ◀️" if r.student_id == highlight else ""
        grade = f" · ⭐{r.avg_grade}" if r.avg_grade is not None else ""
        bold = ("<b>", "</b>") if r.student_id == highlight else ("", "")
        lines.append(
            f"{place_icon(r.place)} {bold[0]}{r.name}{bold[1]} — {r.points} оч. "
            f"({minutes_human(r.minutes)}{grade}){me}"
        )
    if limit and highlight and all(r.student_id != highlight for r in shown):
        mine = next((r for r in rows if r.student_id == highlight), None)
        if mine:
            lines.append("…")
            lines.append(f"{place_icon(mine.place)} <b>{mine.name}</b> — {mine.points} оч. "
                         f"({minutes_human(mine.minutes)}) ◀️")
    if not rows:
        lines.append("Пока никого нет.")
    return "\n".join(lines)


def tasks_text(tasks: list, usage: dict, teacher_names: dict | None = None) -> str:
    if not tasks:
        return "Открытых заданий нет."
    lines = []
    for t in tasks:
        cnt, last = usage.get(t.task_id, (0, ""))
        done = f"сделано {cnt} раз, последний {format_date_display(last)[:5]}" if cnt else "ещё не отрабатывалось"
        who = f" · {teacher_names[t.teacher_id]}" if teacher_names and t.teacher_id in teacher_names else ""
        lines.append(f"• <b>{t.exercise}</b> — {t.minutes} мин{who}\n  <i>{done}</i>")
        if t.comment:
            lines.append(f"  💬 {t.comment}")
    return "\n".join(lines)
