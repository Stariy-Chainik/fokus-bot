"""Идемпотентно добавляет колонку `archived` в лист учеников (12-я).

    .venv/bin/python scripts/setup_student_archive.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_student_archive.py --apply    # добавить колонку

В колонке — дата ухода ученика из школы `YYYY-MM-DD` (решение владельца 21.10.2026: «Удалить ученика» в карточке
= архив). Пусто — ученик в школе. Архивного нет в списках, поиске и начислениях, история занятий и оплат остаётся.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env.prod", override=True)
load_dotenv(".env")

from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from config.settings import settings  # noqa: E402

COLUMN = "archived"


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_students)
    header = ws.row_values(1)
    if COLUMN in header:
        print(f"Колонка «{COLUMN}» уже есть (№{header.index(COLUMN) + 1}) — делать нечего")
        return
    col = len(header) + 1
    if col != 12:
        print(f"Ожидалось 11 колонок до неё, а их {len(header)} — проверьте лист вручную")
        return
    print(f"Лист «{settings.sheet_students}»: {len(header)} колонок, добавим «{COLUMN}» как №{col}")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    if ws.col_count < col:                     # у листа может не быть свободной колонки в сетке
        ws.add_cols(col - ws.col_count)
    ws.update_cell(1, col, COLUMN)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
