from __future__ import annotations

from bot.utils.dates import display_period, format_date_short_with_wd


def build_bill_text(
    student_name: str, group_names: list[str], period_month: str, bills: dict,
) -> tuple[str, int]:
    """Возвращает (text, grand_total) — текст счёта в родительском формате."""
    lines = [
        "📄 <b>Счёт за обучение</b>",
        "",
        f"Ученик: <b>{student_name}</b>",
    ]
    if group_names:
        lines.append("Группы: " + ", ".join(group_names))
    lines.append(f"Месяц: {display_period(period_month)}")
    lines.append("")
    grand_total = 0
    for agg in bills.values():
        grand_total += agg["total"]
        lines.append(f"👨‍🏫 <b>{agg['name']}</b>")
        cur_date: str | None = None
        for b in sorted(agg["items"], key=lambda x: x.date):
            if b.date != cur_date:
                cur_date = b.date
                lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
            lines.append(f"    · {b.duration_min} мин · {b.amount} ₽")
        lines.append(f"  <b>Сумма: {agg['total']} ₽</b>")
        lines.append("")
    lines.append(f"<b>Итого к оплате: {grand_total} ₽</b>")
    return "\n".join(lines), grand_total
