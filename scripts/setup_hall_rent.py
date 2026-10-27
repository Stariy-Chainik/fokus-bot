"""Лист `hall_rent_payments` — аренда зала, полученная от педагогов (bot/services/hall_rent.py). Идемпотентно.

    .venv/bin/python scripts/setup_hall_rent.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from bot.repositories.hall_rent_repo import HEADER  # noqa: E402
from config.settings import settings  # noqa: E402


def main() -> None:
    ensure_sheet(get_sheet(), settings.sheet_hall_rent_payments, HEADER, ["paid_at", "comment"])


if __name__ == "__main__":
    main()
