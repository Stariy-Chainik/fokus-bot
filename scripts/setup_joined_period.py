"""Колонка student_groups.joined_period — месяц вступления ученика в группу. Идемпотентно.

Абонемент начисляется только с этого месяца, поэтому добавление ученика в группу
задним числом больше не создаёт долг за прошлые месяцы.

Существующим строкам проставляется первый месяц занятий их группы — поведение
счётов и долгов не меняется. Дальше даты редактирует админ в карточке группы.

Использование:
    .venv/bin/python scripts/setup_joined_period.py            # показать, что будет
    .venv/bin/python scripts/setup_joined_period.py --apply    # записать
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
COL_NAME = "joined_period"
COL_IDX = 3


def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=scopes)
    return gspread.authorize(creds).open_by_key(settings.spreadsheet_id)


def main() -> None:
    sh = get_sheet()
    ws = sh.worksheet(settings.sheet_student_groups)
    header = ws.row_values(1)
    if COL_NAME not in header:
        if APPLY:
            if ws.col_count < COL_IDX:
                ws.add_cols(COL_IDX - ws.col_count)
            ws.update_cell(1, COL_IDX, COL_NAME)
            print(f"{ws.title}.{COL_NAME}: добавлена колонка {COL_IDX}")
        else:
            print(f"{ws.title}.{COL_NAME}: будет добавлена колонка {COL_IDX}")
    else:
        print(f"{ws.title}.{COL_NAME}: есть (колонка {header.index(COL_NAME) + 1})")

    # Первый месяц занятий каждой группы — из листа занятий
    lessons = sh.worksheet(settings.sheet_lessons).get_all_records()
    first_month: dict[str, str] = {}
    for row in lessons:
        gid = str(row.get("group_id") or "")
        date = str(row.get("date") or "")
        if gid and len(date) >= 7:
            first_month[gid] = min(first_month.get(gid, "9999-99"), date[:7])

    rows = ws.get_all_values()[1:]
    updates, skipped = [], 0
    for i, row in enumerate(rows):
        gid = row[1] if len(row) > 1 else ""
        joined = row[COL_IDX - 1] if len(row) >= COL_IDX else ""
        if joined.strip():
            skipped += 1
            continue
        value = first_month.get(gid, "")
        if not value:
            continue  # у группы нет занятий — оставляем пусто («с начала»)
        updates.append({"range": gspread.utils.rowcol_to_a1(i + 2, COL_IDX), "values": [[value]]})

    print(f"строк: {len(rows)}, уже заполнено: {skipped}, будет проставлено: {len(updates)}")
    if not APPLY:
        print("(сухой прогон, ничего не записано — запустите с --apply)")
        return
    for chunk_start in range(0, len(updates), 200):
        ws.batch_update(updates[chunk_start:chunk_start + 200])
    print(f"записано: {len(updates)}")


if __name__ == "__main__":
    main()
