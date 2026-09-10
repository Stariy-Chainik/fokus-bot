"""Подготовка Google-таблицы к кабинету спортсмена. Идемпотентно.

  1. Лист training_entries (дневник тренировок) с заголовком.
  2. Лист athlete_tasks (задания педагога) с заголовком.
  3. Колонка athlete_tg_id в листе students (9-я).
Колонки дат форматируются как текст, чтобы Sheets не превращал 2026-09-10 в дату.

Использование:
    .venv/bin/python scripts/setup_diary_sheets.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import settings

ENTRY_HEADER = [
    "entry_id", "student_id", "date", "minutes", "topics", "task_ids", "comment",
    "created_at", "grade", "grade_comment", "graded_by", "graded_at",
]
TASK_HEADER = [
    "task_id", "student_id", "teacher_id", "exercise", "minutes", "comment",
    "source", "created_at", "status", "closed_at",
]
TEXT_FMT = {"numberFormat": {"type": "TEXT"}}


def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=scopes)
    return gspread.authorize(creds).open_by_key(settings.spreadsheet_id)


def _col_letter(idx: int) -> str:
    return chr(ord("A") + idx - 1)


def ensure_sheet(sh, title: str, header: list[str], text_cols: list[str]) -> None:
    try:
        ws = sh.worksheet(title)
        current = ws.row_values(1)
        if current[: len(header)] != header:
            if any(current):
                raise SystemExit(f"Лист {title}: заголовок отличается — {current}")
            ws.update(values=[header], range_name="A1")
        print(f"лист {title}: есть")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(header))
        ws.update(values=[header], range_name="A1")
        print(f"лист {title}: создан")
    for col in text_cols:
        idx = header.index(col) + 1
        ws.format(f"{_col_letter(idx)}:{_col_letter(idx)}", TEXT_FMT)


def ensure_students_column(sh) -> None:
    ws = sh.worksheet(settings.sheet_students)
    header = ws.row_values(1)
    if "athlete_tg_id" in header:
        print(f"students.athlete_tg_id: есть (колонка {header.index('athlete_tg_id') + 1})")
        return
    idx = len(header) + 1
    if ws.col_count < idx:
        ws.add_cols(idx - ws.col_count)
    ws.update_cell(1, idx, "athlete_tg_id")
    print(f"students.athlete_tg_id: добавлена колонка {idx}")
    if idx != 9:
        print("ВНИМАНИЕ: ожидалась колонка 9 — поправьте _ATHLETE_TG_ID_COL в student_repo.py")


def main() -> None:
    sh = get_sheet()
    ensure_sheet(sh, settings.sheet_training_entries, ENTRY_HEADER, ["date", "created_at", "graded_at"])
    ensure_sheet(sh, settings.sheet_athlete_tasks, TASK_HEADER, ["created_at", "closed_at"])
    ensure_students_column(sh)


if __name__ == "__main__":
    main()
