"""Закрепить уже принятые оплаты за занятиями, которые они закрыли на момент оплаты (решение владельца 24.10.2026).

    .venv/bin/python scripts/pin_paid_lessons.py [--since 2026-09] [--student STU-0195]            # предпросмотр
    .venv/bin/python scripts/pin_paid_lessons.py [--since 2026-09] [--student STU-0195] --apply    # записать

Для каждой оплаты без списка занятий (`student_period_payments.lesson_ids` пуст) — `payment_ledger.pin_unlinked_rows`:
оплаты по времени, каждая закрывает самые ранние неоплаченные занятия, записанные до неё. Печатает занятия, у которых
меняется отметка оплаты (✅ ↔ ⬜): это места, где занятие задним числом забирало чужие деньги. Пишет между записями
с паузой — квота Google 60 запросов в минуту общая с ботом.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env.prod", override=True)
load_dotenv(".env")

from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot.__main__ import _build_dispatcher  # noqa: E402
from bot.models import Student  # noqa: E402
from bot.services import rate_history  # noqa: E402
from bot.services.payment_ledger import lesson_marks, linked_ids, pin_unlinked_rows  # noqa: E402

WRITE_PAUSE_SEC = 4.0


async def main(since: str, only: str, apply: bool) -> None:
    dp = _build_dispatcher(MemoryStorage())
    rate_history.load(await dp["rate_history_repo"].get_all())
    payment_service, payment_repo = dp["payment_service"], dp["payment_repo"]
    names = {s.student_id: s.name for s in await dp["student_repo"].get_all(include_archived=True)}
    teachers = {t.teacher_id: t.name for t in await dp["teacher_repo"].get_all()}
    lessons = await dp["lesson_repo"].get_all()
    recorded = {ls.lesson_id: ls.recorded_at for ls in lessons}
    targets = sorted({(p.student_id, p.period_month) for p in await payment_repo.get_all()
                      if getattr(p.status, "value", p.status) == "paid" and not p.lesson_id_list
                      and p.period_month >= since and not p.teacher_id.startswith("SUB:")
                      and (not only or p.student_id == only)})
    plan: list[tuple[str, str, str]] = []                  # (student, payment_id, lesson_ids)
    flips = 0
    for sid, ym in targets:
        ledgers = await payment_service.ledger_for(Student(student_id=sid, name=names.get(sid, "")), ym, sync=False)
        for key, led in ledgers.items():
            if led.subscription or not led.items:
                continue
            pinned = pin_unlinked_rows(led.items, led.paid_rows, recorded)
            if not pinned:
                continue
            before = {m["lesson_id"]: m["paid"] for m in lesson_marks(led.items, led.paid, linked_ids(led.paid_rows))}
            after_linked = linked_ids(led.paid_rows) | {i for ids in pinned.values() for i in ids}
            after = {m["lesson_id"]: m["paid"] for m in lesson_marks(led.items, led.paid, after_linked)}
            changed = [lid for lid in before if before[lid] != after[lid]]
            if changed:
                flips += len(changed)
                dates = {b.lesson_id: b.date for b in led.items}
                print(f"{names.get(sid, sid)} · {ym} · {teachers.get(key, key)}: "
                      + ", ".join(f"{dates[lid][8:10]}.{dates[lid][5:7]} {'⬜→✅' if after[lid] else '✅→⬜'}" for lid in changed))
            plan += [(sid, pid, "|".join(ids)) for pid, ids in pinned.items()]
    print(f"\nОплат без привязки: проверено {len(targets)} ученико-месяцев; закрепить строк: {len(plan)}; "
          f"отметок поменяется: {flips}")
    if not apply:
        print("Предпросмотр. Запустите с --apply.")
        return
    for i, (sid, pid, ids) in enumerate(plan, 1):
        await payment_repo.set_lesson_ids(pid, ids, student_id=sid)
        if i % 10 == 0:
            print(f"  записано {i}/{len(plan)}")
        time.sleep(WRITE_PAUSE_SEC)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-09")
    parser.add_argument("--student", default="")
    parser.add_argument("--apply", action="store_true")
    a = parser.parse_args()
    asyncio.run(main(a.since, a.student, a.apply))
