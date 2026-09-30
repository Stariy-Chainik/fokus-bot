"""Занятия ученика за месяц — общий расчёт для карточки ученика у администратора и педагога.

Педагог, группа, длительность, доля ученика и ✓ оплаты (та же отметка, что в счёте,
`student_lesson_marks`). Занятия абонементных/бесплатных групп пишутся без состава —
их показываем по членству ученика в группе («посещение не отмечается»).
"""
from __future__ import annotations

from typing import Callable

from bot.models.enums import LessonType


async def month_lessons(dp, sid: str, period: str, keep: Callable | None = None, money: bool = True) -> dict:
    """keep(lesson) → bool — какие занятия видны (педагогу — его направления); money=False — без сумм."""
    payment_service, lesson_repo = dp["payment_service"], dp["lesson_repo"]
    month = await payment_service.student_lesson_marks(sid, period)
    teachers = {t.teacher_id: t.name for t in await dp["teacher_repo"].get_all()}
    groups = {g.group_id: g for g in await dp["group_repo"].get_all(include_archived=True)}
    seen = {ls.lesson_id for ls in month.lessons}
    rows = [(ls, month.mark(ls.lesson_id), True) for ls in month.lessons]
    membership = await dp["student_group_repo"].get_membership_map()
    my_groups = {gid for (s_id, gid), m in membership.items() if s_id == sid and m.covers(period)}
    for ls in await lesson_repo.get_all():
        if (ls.date[:7] == period and ls.lesson_id not in seen and ls.type == LessonType.GROUP
                and not ls.attendees and ls.group_id in my_groups):
            rows.append((ls, None, False))
    direct = {x.lesson_id: x.amount for row in await payment_service.direct_pay_rows(sid, period) for x in row.lessons}
    out = []
    for ls, mark, attended in sorted(rows, key=lambda r: (r[0].date, r[0].recorded_at or "")):
        if keep is not None and not keep(ls):
            continue
        g = groups.get(ls.group_id)
        out.append({
            "id": ls.lesson_id, "date": ls.date, "durationMin": ls.duration_min, "type": ls.type.value,
            "teacherId": ls.teacher_id, "teacher": teachers.get(ls.teacher_id, ls.teacher_name),
            "group": g.name if g else "", "mode": g.billing_mode.value if g else "",
            "amount": (mark.amount if mark else 0) if money else 0,
            "paid": bool(mark and mark.paid) if money else False,
            "direct": direct.get(ls.lesson_id) if money else None,   # прямая оплата педагогу: сумма родителя
            "attended": attended,                                    # False — группа без отметки посещения
        })
    billed = [x for x in out if x["amount"]]
    return {"period": period, "lessons": out, "money": money,
            "total": sum(x["amount"] for x in billed), "paid": sum(x["amount"] for x in billed if x["paid"])}
