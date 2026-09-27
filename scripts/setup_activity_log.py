"""Лист `activity_log` — лента изменений администратора. Идемпотентно.

    .venv/bin/python scripts/setup_activity_log.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from bot.repositories.activity_log_repo import HEADER  # noqa: E402
from config.settings import settings  # noqa: E402


def main() -> None:
    ensure_sheet(get_sheet(), settings.sheet_activity_log, HEADER, ["ts", "text", "ref"])


if __name__ == "__main__":
    main()
