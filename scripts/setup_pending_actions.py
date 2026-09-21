"""Лист `pending_actions` — очередь решений администратора. Идемпотентно.

Хранит то, что ждёт решения: уведомления об оплате наличными, присланные чеки
и заявки родителей на привязку ребёнка. Раньше они жили только в сообщениях
Telegram, поэтому в кабинете их не было видно.

    .venv/bin/python scripts/setup_pending_actions.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_diary_sheets import ensure_sheet, get_sheet  # noqa: E402
from config.settings import settings  # noqa: E402

HEADER = [
    "action_id", "kind", "student_id", "student_name", "period_month", "amount", "method",
    "parent_addr", "file_id", "file_type", "comment", "created_at", "status",
    "decided_at", "decided_by_tg_id",
]


def main() -> None:
    ensure_sheet(get_sheet(), settings.sheet_pending_actions, HEADER,
                 ["period_month", "created_at", "decided_at", "parent_addr", "file_id"])


if __name__ == "__main__":
    main()
