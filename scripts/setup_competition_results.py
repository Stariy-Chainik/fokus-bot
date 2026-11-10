"""Листы `competition_results` и `competition_scans` — результаты турниров ФТСАРР у наших учеников
(bot/services/competition_results.py). Идемпотентно.

    .venv/bin/python scripts/setup_competition_results.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from bot.repositories.competition_repo import HEADER, SCAN_HEADER  # noqa: E402
from config.settings import settings  # noqa: E402


def main() -> None:
    sh = get_sheet()
    # текстовые колонки: дата и класс иначе станут датой/числом, points — «3.30» не должен стать 3 марта
    ensure_sheet(sh, settings.sheet_competition_results, HEADER,
                 ["date", "group_name", "program", "class", "points", "coaches", "fetched_at"])
    ensure_sheet(sh, settings.sheet_competition_scans, SCAN_HEADER, ["date", "scanned_at"])


if __name__ == "__main__":
    main()
