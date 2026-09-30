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


# добавлены позже: за каких педагогов платил родитель; у какого педагога наличные (ждут администратора)
EXTRA_COLUMNS = ["teacher_keys", "held_by"]


def ensure_columns(sh) -> None:
    ws = sh.worksheet(settings.sheet_pending_actions)
    header = ws.row_values(1)
    for name in EXTRA_COLUMNS:
        if name in header:
            print(f"{settings.sheet_pending_actions}.{name}: есть (колонка {header.index(name) + 1})")
            continue
        idx = len(header) + 1
        if ws.col_count < idx:
            ws.add_cols(idx - ws.col_count)
        ws.update_cell(1, idx, name)
        header.append(name)
        print(f"{settings.sheet_pending_actions}.{name}: добавлена колонка {idx}")


def main() -> None:
    sh = get_sheet()
    ensure_sheet(sh, settings.sheet_pending_actions, HEADER,
                 ["period_month", "created_at", "decided_at", "parent_addr", "file_id"])
    ensure_columns(sh)


if __name__ == "__main__":
    main()
