"""Абонемент «2 / 3 раза в неделю» (SUBSCRIPTION_TWICE_PRICES): общая логика для админа и старшего тренера.

«2 раза» — постоянное персональное правило цены ученика (period '*') с месяца `since`; «3 раза» — правило
снимается, а месяцы, когда действовало «2 раза», закрепляются помесячными переопределениями той же ценой,
поэтому прошлые счета не меняются.
"""
from __future__ import annotations

from config.settings import settings

from . import activity


async def frequency_info(override_repo, sid: str, group) -> dict:
    twice = settings.subscription_twice_map.get(group.group_id) if group is not None else None
    if not twice:
        return {}
    rule = next((o for o in await override_repo.get_for_group(group.group_id)
                 if o.period_month == "*" and o.student_id == sid), None)
    return {"freq": {"times": 2 if rule and rule.amount == twice else 3,
                     "since": (rule.created_at or "")[:7] if rule else "",
                     "priceTwice": twice, "priceThrice": group.price_full}}


async def set_frequency(override_repo, sid: str, gid: str, times: int, since: str, actor: int = 0) -> bool:
    twice = settings.subscription_twice_map.get(gid or "")
    if not twice or times not in (2, 3) or len(since or "") != 7:
        return False
    if times == 2:
        await override_repo.upsert(gid, "*", sid, twice, since=since)
    else:
        rule = next((o for o in await override_repo.get_for_group(gid) if o.period_month == "*" and o.student_id == sid), None)
        if rule is not None:
            start = (rule.created_at or "")[:7] or since
            y, m = int(start[:4]), int(start[5:7])
            existing = {o.period_month for o in await override_repo.get_for_group(gid) if o.student_id == sid}
            while f"{y}-{m:02d}" < since:
                if f"{y}-{m:02d}" not in existing:
                    await override_repo.upsert(gid, f"{y}-{m:02d}", sid, rule.amount)
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            await override_repo.delete(gid, "*", sid)
    await activity.record(activity.STUDENT, f"{sid} в группе {gid}: {times} раза в неделю с {since}", actor=actor, ref=sid)
    return True
