"""Идемпотентно добавляет в лист учеников колонки `dance_class` (13-я) и `sport_rank` (14-я).

    .venv/bin/python scripts/setup_dance_rank.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_dance_rank.py --apply    # добавить колонки

Танцевальный класс (Н-2 … M) и спортивный разряд (юношеские и взрослые, КМС, МС …) ученика бальных танцев
(решение владельца 08.11.2026). Пусто — не присвоен. Правятся в карточке ученика кабинета администратора и педагога,
см. bot/services/dance_rank.py.
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

COLUMNS = ("dance_class", "sport_rank")
BEFORE = 12   # сколько колонок должно быть до них


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_students)
    header = ws.row_values(1)
    missing = [c for c in COLUMNS if c not in header]
    if not missing:
        print("Колонки «dance_class» и «sport_rank» уже есть — делать нечего")
        return
    if len(header) != BEFORE + len(COLUMNS) - len(missing):
        print(f"Ожидалось {BEFORE + len(COLUMNS) - len(missing)} колонок, а их {len(header)} — проверьте лист вручную")
        return
    print(f"Лист «{settings.sheet_students}»: {len(header)} колонок, добавим {', '.join(missing)} "
          f"как №{len(header) + 1}–{len(header) + len(missing)}")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    need = len(header) + len(missing)
    if ws.col_count < need:                     # у листа может не быть свободных колонок в сетке
        ws.add_cols(need - ws.col_count)
    for i, name in enumerate(missing, start=len(header) + 1):
        ws.update_cell(1, i, name)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
