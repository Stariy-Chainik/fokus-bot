"""Колонка groups.archived — архив групп. Идемпотентно.

Архивная группа («1» в колонке) скрыта из списков записи занятий, счетов и
добавления учеников, но строка остаётся: занятия, оплаты и зарплаты прошлых
месяцев считаются как прежде. Снимается кнопкой «♻️ Вернуть из архива».

Использование:
    .venv/bin/python scripts/setup_group_archive.py            # показать, что будет
    .venv/bin/python scripts/setup_group_archive.py --apply    # записать
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import settings

APPLY = "--apply" in sys.argv
COL_NAME = "archived"
COL_IDX = 12


def main() -> None:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=scopes)
    ws = gspread.authorize(creds).open_by_key(settings.spreadsheet_id).worksheet(settings.sheet_groups)
    header = ws.row_values(1)
    if COL_NAME in header:
        print(f"{ws.title}.{COL_NAME}: есть (колонка {header.index(COL_NAME) + 1})")
        return
    if not APPLY:
        print(f"{ws.title}.{COL_NAME}: будет добавлена колонка {COL_IDX}")
        return
    if ws.col_count < COL_IDX:
        ws.add_cols(COL_IDX - ws.col_count)
    ws.update_cell(1, COL_IDX, COL_NAME)
    print(f"{ws.title}.{COL_NAME}: добавлена колонка {COL_IDX}")


if __name__ == "__main__":
    main()
