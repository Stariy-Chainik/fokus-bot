"""Идемпотентно добавляет колонку `lesson_ids` в лист оплат (15-я).

    .venv/bin/python scripts/setup_payment_lessons.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_payment_lessons.py --apply    # добавить колонку

В колонке — занятия (LES-id через «|»), за которые принята оплата. У строки-остатка
это намерение плательщика: какие занятия он выбрал к оплате. Пустое значение = прежнее
поведение (оплата закрывает занятия месяца с самых ранних).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from config.settings import settings  # noqa: E402

COLUMN = "lesson_ids"


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_payments)
    header = ws.row_values(1)
    if COLUMN in header:
        print(f"Колонка «{COLUMN}» уже есть (№{header.index(COLUMN) + 1}) — делать нечего")
        return
    col = len(header) + 1
    print(f"Лист «{settings.sheet_payments}»: {len(header)} колонок, добавим «{COLUMN}» как №{col}")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    ws.update_cell(1, col, COLUMN)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
