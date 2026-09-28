"""Лист `group_schedule` — расписание групп для напоминаний педагогам. Идемпотентно.

    .venv/bin/python scripts/setup_group_schedule.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from bot.repositories.group_schedule_repo import HEADER  # noqa: E402
from config.settings import settings  # noqa: E402


def main() -> None:
    ensure_sheet(get_sheet(), settings.sheet_group_schedule, HEADER, ["slot_id", "start", "end"])


if __name__ == "__main__":
    main()
