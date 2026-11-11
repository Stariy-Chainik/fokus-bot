"""Лист `parent_feedback` — обращения родителей к руководителю студии (bot/services/parent_feedback.py). Идемпотентно.

    .venv/bin/python scripts/setup_parent_feedback.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from bot.repositories.parent_feedback_repo import HEADER  # noqa: E402
from config.settings import settings  # noqa: E402


def main() -> None:
    ensure_sheet(get_sheet(), settings.sheet_parent_feedback, HEADER,
                 ["ts", "parent_addr", "text", "reply", "replied_at"])


if __name__ == "__main__":
    main()
