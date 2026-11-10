"""История результатов турниров ФТСАРР за прошлые месяцы (bot/services/competition_results.py).

    .venv/bin/python scripts/import_ftsarr_results.py --since 2026-01 [--until 2026-11] [--regions 50,77|all]   # предпросмотр
    .venv/bin/python scripts/import_ftsarr_results.py --since 2026-01 --apply                                   # записать

Обходит календарь ФТСАРР по месяцам (регионы — как FTSARR_REGIONS, `all` — все) и печатает выступления учеников
бота: турнир, группа, пара, место. С `--apply` пишет строки в `competition_results` и отмечает турниры в
`competition_scans`; уже обойдённые турниры пропускает (`--force` — обойти заново). Сообщений никому не шлёт.
Предпросмотр листы не трогает (репозитории подменяются памятью). Квота Google: одна-две записи на турнир;
к ФТСАРР — пауза 0,15 с между запросами. Не запускать одновременно с деплоем.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env.prod", override=True)
load_dotenv(".env")

from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot.__main__ import _build_dispatcher  # noqa: E402
from bot.repositories.competition_repo import CompetitionResult, CompetitionScan  # noqa: E402
from bot.services import competition_results  # noqa: E402
from config.settings import settings  # noqa: E402


class _MemoryResults:
    """Предпросмотр: лист результатов в памяти, ничего не пишем."""

    def __init__(self) -> None:
        self.items: list = []

    async def get_all(self):
        return list(self.items)

    async def upsert_many(self, rows):
        existing = {r.key for r in self.items}
        added = []
        for r in rows:
            if r.key in existing:
                continue
            new = CompetitionResult(**{**r.__dict__, "result_id": f"CR-{len(self.items) + 1:06d}"})
            self.items.append(new)
            existing.add(new.key)
            added.append(new)
        return added, 0


class _MemoryScans:
    def __init__(self) -> None:
        self.items: list = []

    async def scanned_ids(self):
        return {s.competition_id for s in self.items}

    async def mark(self, competition_id, name, date, city, groups, hits):
        self.items.append(CompetitionScan(competition_id, name, date, city, "", groups, hits))


def _months(since: str, until: str) -> list[str]:
    y, m = int(since[:4]), int(since[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= until:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


async def main(since: str, until: str, regions: str | None, force: bool, apply: bool) -> None:
    dp = _build_dispatcher(MemoryStorage())
    if not apply:
        dp["competition_result_repo"] = _MemoryResults()
        dp["competition_scan_repo"] = _MemoryScans()
    region_list = None
    if regions is not None:
        region_list = [] if regions.strip().lower() in ("all", "все") else [r.strip() for r in regions.split(",") if r.strip()]
    months = _months(since, until)
    print(f"Месяцы: {', '.join(months)} · регионы: {regions or settings.ftsarr_regions} · "
          f"{'ЗАПИСЬ' if apply else 'предпросмотр'}")
    names = {s.student_id: s.name for s in await dp["student_repo"].get_all(include_archived=True)}
    stats = await competition_results.sync(dp, months, region_list, force=force or not apply, notify=False, log=print)
    for o in stats.outcomes:
        if not o.rows:
            continue
        print(f"\n{o.competition.start_date} · {o.competition.city} · {o.competition.name}")
        for line in competition_results._pair_lines(o.rows, names):
            print("   ", line.replace("<b>", "").replace("</b>", ""))
    print(f"\nИтого: турниров в календаре {stats.competitions}, обойдено {stats.scanned}, пропущено (уже были) "
          f"{stats.skipped}, ещё идут {stats.pending}, выступлений {stats.rows}, новых строк {stats.added}")
    if not apply:
        print("Это предпросмотр — в листы ничего не записано. Добавьте --apply.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="первый месяц, YYYY-MM")
    ap.add_argument("--until", default=competition_results.months_back(1)[0], help="последний месяц, YYYY-MM")
    ap.add_argument("--regions", default=None, help="коды регионов через запятую или all; по умолчанию FTSARR_REGIONS")
    ap.add_argument("--force", action="store_true", help="обойти и уже обойдённые турниры")
    ap.add_argument("--apply", action="store_true", help="записать в листы")
    a = ap.parse_args()
    asyncio.run(main(a.since, a.until, a.regions, a.force, a.apply))
