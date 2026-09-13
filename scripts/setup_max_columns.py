"""Колонки для родителей в MAX. Идемпотентно.

  students.parent_max_ids — 10-я колонка (список max_id через |)
  clients.max_id          — 7-я колонка

Использование:
    .venv/bin/python scripts/setup_max_columns.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import settings


def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=scopes)
    return gspread.authorize(creds).open_by_key(settings.spreadsheet_id)


def ensure_column(ws, name: str, expected_idx: int) -> None:
    header = ws.row_values(1)
    if name in header:
        print(f"{ws.title}.{name}: есть (колонка {header.index(name) + 1})")
        return
    idx = len(header) + 1
    while idx < expected_idx:  # заполняем пропуски пустыми заголовками не будем — сигналим
        print(f"ВНИМАНИЕ: в {ws.title} только {len(header)} колонок, ожидалось {expected_idx - 1}")
        break
    if ws.col_count < idx:
        ws.add_cols(idx - ws.col_count)
    ws.update_cell(1, idx, name)
    note = "" if idx == expected_idx else f" (ожидалась {expected_idx} — поправьте константу в репозитории)"
    print(f"{ws.title}.{name}: добавлена колонка {idx}" + note)


def main() -> None:
    sh = get_sheet()
    ensure_column(sh.worksheet(settings.sheet_students), "parent_max_ids", 10)
    ensure_column(sh.worksheet(settings.sheet_clients), "max_id", 7)


if __name__ == "__main__":
    main()
