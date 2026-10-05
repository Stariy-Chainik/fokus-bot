from __future__ import annotations

from bot.utils.dates import display_period, format_date_short_with_wd


def payment_purpose(student_name: str, period_month: str) -> str:
    """Назначение платежа: одно и то же в QR, в тексте реквизитов и в СБП — админ по нему видит, за кого платёж."""
    return f"Оплата занятий, {student_name.strip()}, {display_period(period_month)}"


def build_bill_text(
    student_name: str, group_names: list[str], period_month: str, bills: dict,  # {ключ → BillAggregate}
    paid: int = 0,
) -> tuple[str, int]:
    """Возвращает (text, grand_total) — текст счёта в родительском формате.
    paid — уже оплачено за месяц (накопительный счёт): печатаем оплачено / остаток."""
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
        grand_total += agg.total
        if agg.subscription:
            # Абонемент: фикс-сумма за месяц, без разбивки по датам.
            lines.append(f"💳 <b>{agg.name}</b>")
            lines.append("    · фиксированная сумма за месяц")
            lines.append(f"  <b>Сумма: {agg.total} ₽</b>")
            lines.append("")
            continue
        lines.append(f"{'👥' if agg.group else '👨‍🏫'} <b>{agg.name}</b>")
        cur_date: str | None = None
        for b in sorted(agg.items, key=lambda x: x.date):
            if b.date != cur_date:
                cur_date = b.date
                lines.append(f"  📅 <b>{format_date_short_with_wd(b.date)}</b>")
            lines.append(f"    · {b.duration_min} мин · {b.amount} ₽")
        lines.append(f"  <b>Сумма: {agg.total} ₽</b>")
        lines.append("")
    if paid > 0:
        lines.append(f"Начислено: {grand_total} ₽ · оплачено: {paid} ₽")
        lines.append(f"<b>Остаток к оплате: {max(grand_total - paid, 0)} ₽</b>")
    else:
        lines.append(f"<b>Итого к оплате: {grand_total} ₽</b>")
    return "\n".join(lines), grand_total


_DIRECTIONS = (
    (("современ",), "современные танцы"),
    (("хг", "гимнаст"), "художественная гимнастика"),
    (("хореограф",), "хореография"),
    (("джаз",), "джаз"),
    (("офп",), "ОФП"),
    (("бт", "бальн"), "бальные танцы"),
)


def direction_of(group_name: str) -> str:
    """Направление по названию группы: «ВБ ХГ Старшая» → художественная гимнастика, «БП БТ …» →
    бальные танцы, «ВБ Современные …» → современные танцы. Пусто — не распознано."""
    words = {"".join(ch for ch in w if ch.isalnum()).lower() for w in group_name.split()}
    low = group_name.lower()
    for needles, label in _DIRECTIONS:
        if any(n in words or (len(n) > 2 and n in low) for n in needles):
            return label
    return ""


def online_purpose(student_name: str, periods: list[str], subscription_dirs: list[str], lesson_dirs: list[str]) -> str:
    """Назначение платежа ЮКассы (решение владельца 05.10.2026): «Абонемент — художественная гимнастика,
    сентябрь 2026 — Авалян Жанна». Абонемент и занятия вместе → «Абонемент и занятия»."""
    from bot.utils.dates import period_label
    dirs = [d for d in dict.fromkeys(subscription_dirs + lesson_dirs) if d]
    what = "Абонемент" if subscription_dirs and not lesson_dirs else "Занятия" if not subscription_dirs else "Абонемент и занятия"
    head = f"{what} — {', '.join(dirs)}" if dirs else what
    months = ", ".join(period_label(p).lower() for p in periods)
    return f"{head}, {months} — {student_name}"
