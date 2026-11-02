"""Сводка зарплаты «по ставкам»: строки SalaryService, сложенные по правилу начисления.

Экран зарплаты показывал у Яковлевой 84 строки за месяц, из них 42 нулевых «в смене», а по какой ставке
начислено — нигде не было написано (решение владельца 31.10.2026). Здесь те же строки складываются в одну
на правило: «Смена Боброво — 14 дней × 3 ч, ставка группы 1400 ₽/ч», «ЮБ сад ХГ — 10 занятий, ставка педагога
1700 ₽/ч = 1700 ₽ за 60 мин», «ХГ Индивидуальные — 50 % от сбора 23 400 ₽». Внутри строки — занятия по датам.
Чистая функция: имена групп и учеников приходят параметрами, суммы — из строк SalaryService, правило —
из `billing_service.salary_rule` (та же логика, что `calc_earned`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.models.enums import LessonType
from bot.services.billing_service import salary_rule
from bot.utils.attendees import parse_attendees
from bot.utils.constants import MINUTES_PER_UNIT
from bot.utils.groups import lesson_group_title

NO_PAY = "без оплаты"
MERGED_NOTE = "две группы одним занятием"


@dataclass
class SummaryItem:
    date: str
    label: str
    minutes: int
    amount: int
    lesson_id: str | None = None
    note: str = ""


@dataclass
class SummaryRow:
    key: str
    kind: str            # shift | group | individual | revenue_share | sub_share | direct | override | bonus
    title: str
    rule: str = ""
    count: int = 0
    minutes: int = 0
    amount: int = 0
    group_id: str = ""
    free: int = 0        # занятий без оплаты (ставка 0 или зарплатная длительность 0)
    merged: int = 0      # объединённых занятий двух групп — оплачены как одно
    items: list[SummaryItem] = field(default_factory=list)
    _rates: dict[int, int] = field(default_factory=dict)        # ставка за 45 мин → сколько занятий
    _durations: dict[int, int] = field(default_factory=dict)    # длительность → сколько занятий (оплаченных)
    _group_rate: bool = False
    _fixed_minutes: int | None = None
    _percent: int = 0
    _revenue: int = 0
    _headcount_min: int = 0     # GROUP_HEADCOUNT_RATES: от скольких детей повышенная ставка
    _headcount_rate: int = 0
    _headcount_hits: int = 0    # занятий по повышенной ставке

    def add(self, item: SummaryItem) -> None:
        self.items.append(item)
        self.count += 1
        self.minutes += item.minutes
        self.amount += item.amount


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def rate_text(rate45: int) -> str:
    """1050 → «1400 ₽/ч», 1275 → «1700 ₽/ч», 1000 → «1000 ₽ за 45 мин» (в час не делится нацело)."""
    per_hour = rate45 * 60
    if per_hour % MINUTES_PER_UNIT == 0:
        return f"{_fmt(per_hour // MINUTES_PER_UNIT)} ₽/ч"
    return f"{_fmt(rate45)} ₽ за 45 мин"


def plural(n: int, one: str, few: str, many: str) -> str:
    """12 детей, 21 ребёнок, 3 ребёнка."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def kids_text(n: int) -> str:
    return plural(n, "ребёнок", "ребёнка", "детей")


def hours_text(minutes: int) -> str:
    return f"{minutes // 60} ч" + (f" {minutes % 60} мин" if minutes % 60 else "")


def _lesson_label(ls, group_names: dict, student_names: dict) -> str:
    if ls.type == LessonType.GROUP:
        return lesson_group_title(ls, group_names, "Группа")
    who = [n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n]
    return " + ".join(who) or student_names.get(ls.student_1_id or "", "Занятие")


def _attendee_names(ls, student_names: dict) -> str:
    ids = [e.student_id for e in parse_attendees(ls.attendees or "", default_duration=ls.duration_min)]
    return " + ".join(student_names.get(i, i) for i in ids)


def _rate_rule(row: SummaryRow) -> str:
    """Словами: откуда ставка и сколько выходит за занятие."""
    who = "ставка группы" if row._group_rate else "ставка педагога"
    rates = [r for r, n in sorted(row._rates.items(), key=lambda kv: -kv[1]) if r > 0]
    if row._fixed_minutes == 0:
        return "в зарплату не идёт"
    if not rates:
        return NO_PAY
    if row._headcount_min:
        base = [r for r in rates if r != row._headcount_rate]
        boost = f"от {row._headcount_min} детей — {rate_text(row._headcount_rate)}"
        if not base:
            return f"{boost} · все занятия"
        hits = f" ({plural(row._headcount_hits, 'занятие', 'занятия', 'занятий')})" if row._headcount_hits else ""
        return f"{who} {' / '.join(rate_text(r) for r in base)} · {boost}{hits}"
    if row._fixed_minutes:
        per_lesson = " / ".join(_fmt(round(r * row._fixed_minutes / MINUTES_PER_UNIT)) for r in rates)
        return f"{per_lesson} ₽ за занятие любой длительности · {who} {' / '.join(rate_text(r) for r in rates)}"
    text = f"{who} {' / '.join(rate_text(r) for r in rates)}"
    if len(rates) == 1 and len(row._durations) == 1:
        dur = next(iter(row._durations))
        if not (dur == 60 and rate_text(rates[0]).endswith("/ч")):      # «1700 ₽/ч = 1700 ₽ за 60 мин» — масло масляное
            text += f" = {_fmt(round(rates[0] * dur / MINUTES_PER_UNIT))} ₽ за {dur} мин"
    elif len(rates) == 1:
        text += " × длительность"
    return text


def summarize(lines, lessons: dict, teacher, group_names: dict, student_names: dict,
              *, for_teacher: bool = False) -> list[SummaryRow]:
    """lines — SalaryService.lines_for(); lessons — {lesson_id: Lesson} того же месяца.

    for_teacher — педагогу процент от абонементов показывается без начислений группы
    (решение владельца 12.10.2026)."""
    rows: dict[str, SummaryRow] = {}

    def row(key: str, kind: str, title: str, group_id: str = "") -> SummaryRow:
        if key not in rows:
            rows[key] = SummaryRow(key=key, kind=kind, title=title, group_id=group_id)
        return rows[key]

    in_shift: dict[str, list[str]] = {}            # дата → подписи групп смены
    in_share: dict[str, list[SummaryItem]] = {}    # группа → занятия, вошедшие в процент
    for ln in lines:
        ls = lessons.get(ln.lesson_id or "")
        if ln.kind == "in_shift" and ls is not None:
            in_shift.setdefault(ln.date, []).append(f"{lesson_group_title(ls, group_names, 'Группа')} {ls.duration_min} мин")
        elif ln.kind == "in_share" and ls is not None:
            in_share.setdefault(ls.group_id, []).append(SummaryItem(
                ln.date, lesson_group_title(ls, group_names, "Группа"), ls.duration_min, 0, ls.lesson_id,
                "входит в оплату группы за месяц"))
        elif ln.kind == "lesson":
            _add_lesson(row, ln, ls, teacher, group_names, student_names)

    for ln in lines:
        if ln.kind == "shift":
            rate45 = round(ln.amount * MINUTES_PER_UNIT / ln.minutes) if ln.minutes else 0
            title = ln.label.rsplit(" — ", 1)[0] if " — " in ln.label else (ln.label or "Смена")
            r = row(f"shift:{title}:{rate45}", "shift", title)
            r._rates[rate45] = r._rates.get(rate45, 0) + 1
            r.add(SummaryItem(ln.date, " · ".join([hours_text(ln.minutes)] + sorted(in_shift.get(ln.date, []))),
                              ln.minutes, ln.amount))
        elif ln.kind == "override":
            r = row("override", "override", "Нестандартные дни")
            r.rule = "минуты дня заданы вручную вместо расчёта по группам"
            label = ln.label.rsplit(" — ", 1)[0].replace("Корректировка: ", "").replace("Корректировка дня", "")
            r.add(SummaryItem(ln.date, " · ".join(x for x in (hours_text(ln.minutes), label) if x), ln.minutes, ln.amount))
        elif ln.kind == "share":
            name = group_names.get(ln.group_id, ln.group_id)
            r = row(f"share:{ln.group_id}", "sub_share", name, ln.group_id)
            r.rule = "процент от абонементов группы за месяц" if for_teacher else ln.label
            r.amount += ln.amount
            for item in in_share.pop(ln.group_id, []):
                r.items.append(item)
                r.count += 1
                r.minutes += item.minutes
        elif ln.kind == "bonus":
            r = row("bonus", "bonus", "Премии")
            r.rule = "сверх зарплаты · выплачены сразу"
            r.add(SummaryItem(ln.date, ln.label.replace("Премия: ", "") or "Премия", 0, ln.amount))
    for gid, items in in_share.items():          # процент без строки share (начислений группы нет)
        r = row(f"share:{gid}", "sub_share", group_names.get(gid, gid), gid)
        r.rule = "процент от абонементов группы за месяц"
        for item in items:
            r.items.append(item)
            r.count += 1
            r.minutes += item.minutes

    out = []
    for r in rows.values():
        if r.kind == "shift":
            rate45 = next(iter(r._rates))
            r.rule = f"{rate_text(rate45)} за время смены · группы внахлёст считаются один раз"
        elif r.kind in ("group", "individual"):
            r.rule = _rate_rule(r)
        elif r.kind == "revenue_share":
            r.rule = f"{r._percent} % от сбора с учениц · сбор {_fmt(r._revenue)} ₽"
        elif r.kind == "direct":
            r.rule = "родители платят педагогу напрямую · школа не начисляет"
        r.items.sort(key=lambda i: (i.date, i.lesson_id or ""))
        out.append(r)
    return sorted(out, key=lambda r: (-r.amount, r.kind == "direct", r.title))


def _add_lesson(row, ln, ls, teacher, group_names: dict, student_names: dict) -> None:
    if ls is None:
        r = row("other", "individual", "Занятия")
        r.add(SummaryItem(ln.date, ln.label, ln.minutes, ln.amount, ln.lesson_id))
        return
    rule = salary_rule(ls.type, ls.duration_min, teacher, ls.group_id or "", ls.attendees, ls.date)
    label = _lesson_label(ls, group_names, student_names)
    if rule.kind == "share":
        r = row(f"rshare:{ls.group_id}", "revenue_share", group_names.get(ls.group_id, label), ls.group_id)
        r._percent, r._revenue = rule.percent, r._revenue + rule.revenue
        r.add(SummaryItem(ln.date, _attendee_names(ls, student_names) or label, ls.duration_min, ln.amount, ls.lesson_id,
                          f"сбор {_fmt(rule.revenue)} ₽"))
        return
    if rule.kind == "direct":
        r = row("direct", "direct", "Индивидуальные — прямая оплата")
        r.add(SummaryItem(ln.date, label, ls.duration_min, 0, ls.lesson_id))
        return
    if ls.type == LessonType.GROUP:
        r = row(f"g:{ls.group_id}", "group", group_names.get(ls.group_id, label), ls.group_id)
    else:
        r = row("ind", "individual", "Индивидуальные и парные")
    r._group_rate = r._group_rate or rule.group_rate
    if rule.fixed:
        r._fixed_minutes = rule.minutes
    r._rates[rule.rate] = r._rates.get(rule.rate, 0) + 1
    notes = []
    if ln.amount == 0:
        r.free += 1
        notes.append(NO_PAY)
    else:
        r._durations[ls.duration_min] = r._durations.get(ls.duration_min, 0) + 1
    if ls.merged_groups:
        r.merged += 1
        notes.append(MERGED_NOTE)
    if rule.headcount_min:
        r._headcount_min, r._headcount_rate = rule.headcount_min, rule.headcount_rate
        boosted = rule.headcount >= rule.headcount_min and ln.amount > 0
        r._headcount_hits += boosted
        notes.append(kids_text(rule.headcount) + (f" — {rate_text(rule.headcount_rate)}" if boosted else ""))
    r.add(SummaryItem(ln.date, label, ls.duration_min, ln.amount, ls.lesson_id, " · ".join(notes)))


def summary_dicts(rows: list[SummaryRow]) -> list[dict]:
    """В JSON для кабинета (служебные поля с подчёркиванием не отдаются)."""
    return [{
        "key": r.key, "kind": r.kind, "title": r.title, "rule": r.rule, "count": r.count, "minutes": r.minutes,
        "amount": r.amount, "groupId": r.group_id, "free": r.free, "merged": r.merged,
        "items": [{"date": i.date, "label": i.label, "minutes": i.minutes, "amount": i.amount,
                   "lessonId": i.lesson_id, "note": i.note} for i in r.items],
    } for r in rows]
