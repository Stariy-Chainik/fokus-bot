"""Идемпотентно добавляет в лист учеников 19-ю колонку `birth_date` — дата рождения (`YYYY-MM-DD`).

    .venv/bin/python scripts/setup_birth_date.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_birth_date.py --apply    # применить

Решение владельца 08.11.2026: по дате рождения кабинет показывает возраст, возрастную группу ФТСАРР у бальников и
дни рождения. Ожидает, что колонки 13–18 бальных танцев уже на месте (scripts/setup_dance_rank.py).
См. bot/services/birthdays.py.
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

COL = 19
NAME = "birth_date"


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_students)
    header = ws.row_values(1)
    if len(header) >= COL and header[COL - 1] == NAME:
        print(f"Колонка №{COL} «{NAME}» уже на месте — делать нечего")
        return
    if len(header) >= COL and header[COL - 1]:
        print(f"В колонке №{COL} уже заголовок «{header[COL - 1]}» — проверьте лист вручную")
        return
    if len(header) < COL - 1 or header[COL - 2] != "ftsarr_id":
        print(f"Ожидали «ftsarr_id» в колонке №{COL - 1}, а лист имеет {len(header)} колонок — сначала scripts/setup_dance_rank.py")
        return
    print(f"Лист «{settings.sheet_students}»: {len(header)} колонок. Добавим «{NAME}» как №{COL}.")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    if ws.col_count < COL:
        ws.add_cols(COL - ws.col_count)
    ws.update_cell(1, COL, NAME)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
